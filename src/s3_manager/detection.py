"""Object detection with AWS Lambda: deploy the function, connect it to a bucket, read its results.

The function itself is aws_lambda/object_detection.py. After `deploy_object_detection` every new
.jpg / .jpeg / .png in the bucket triggers it, and it saves the detected objects as JSON next to
the image (photos/cat.jpg -> photos/cat.json).
"""

import io
import json
import logging
import time
import zipfile
from pathlib import Path

import requests
from botocore.exceptions import ClientError

from s3_manager import objects

logger = logging.getLogger(__name__)

HANDLER_FILE = Path(__file__).resolve().parents[2] / "aws_lambda" / "object_detection.py"

DEFAULT_FUNCTION_NAME = "object-detection"
LAMBDA_RUNTIME = "python3.13"
LAMBDA_HANDLER = "object_detection.lambda_handler"  # <file name without .py>.<function name>
LAMBDA_TIMEOUT = 30  # seconds
LAMBDA_MEMORY = 256  # MB

# AWS Academy / Learner Lab accounts can't create IAM roles, but every account has LabRole.
DEFAULT_ROLE_NAME = "LabRole"

PROVIDERS = ("api-ninjas", "huggingface")
PROVIDER_KEYS = {"api-ninjas": "API_NINJAS_KEY", "huggingface": "HF_API_TOKEN"}
API_NINJAS_URL = "https://api.api-ninjas.com/v1/objectdetection"
HF_API_URL = "https://router.huggingface.co/hf-inference/models/facebook/detr-resnet-50"

# The same list as in the Lambda function. S3 suffix filters are case-sensitive, so both cases.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
TRIGGER_EXTENSIONS = IMAGE_EXTENSIONS + tuple(extension.upper() for extension in IMAGE_EXTENSIONS)
TRIGGER_ID_PREFIX = "object-detection-"
NOTIFICATION_KEYS = (
    "TopicConfigurations",
    "QueueConfigurations",
    "LambdaFunctionConfigurations",
    "EventBridgeConfiguration",
)


def build_lambda_zip(handler_file=HANDLER_FILE):
    """Return a zip file (as bytes) with the handler at its top, the way Lambda expects it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(handler_file, arcname=Path(handler_file).name)
    return buffer.getvalue()


def get_account_id(sts_client):
    """Return the 12-digit id of the AWS account the credentials belong to."""
    return sts_client.get_caller_identity()["Account"]


def default_role_arn(account_id):
    """Return the ARN of LabRole, the role AWS Academy accounts give to Lambda functions."""
    return f"arn:aws:iam::{account_id}:role/{DEFAULT_ROLE_NAME}"


def detection_environment(provider, api_key):
    """Return the environment variables for the function: the provider and its key."""
    if provider not in PROVIDERS:
        raise ValueError(f"Provider must be one of {', '.join(PROVIDERS)}, got '{provider}'.")
    return {"PROVIDER": provider, PROVIDER_KEYS[provider]: api_key}


def function_exists(lambda_client, function_name):
    try:
        lambda_client.get_function(FunctionName=function_name)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def deploy_detection_lambda(
    lambda_client,
    zip_bytes,
    role_arn,
    function_name=DEFAULT_FUNCTION_NAME,
    environment=None,
    handler=LAMBDA_HANDLER,
    description="Detects objects on images uploaded to S3 (s3-manager).",
):
    """Create the Lambda function, or update its code and settings if it exists.

    Returns (function ARN, True if it was created). Waits until the function is ready, because
    Lambda refuses changes while a create or update is still in progress.
    handler and description let other functions (organizer.py) be deployed the same way.
    """
    settings = {
        "Role": role_arn,
        "Runtime": LAMBDA_RUNTIME,
        "Handler": handler,
        "Timeout": LAMBDA_TIMEOUT,
        "MemorySize": LAMBDA_MEMORY,
        "Environment": {"Variables": environment or {}},
    }

    if function_exists(lambda_client, function_name):
        logger.info("Updating Lambda function '%s'...", function_name)
        lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        lambda_client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        response = lambda_client.update_function_configuration(FunctionName=function_name, **settings)
        lambda_client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        return response["FunctionArn"], False

    logger.info("Creating Lambda function '%s'...", function_name)
    response = lambda_client.create_function(
        FunctionName=function_name,
        Code={"ZipFile": zip_bytes},
        Description=description,
        **settings,
    )
    lambda_client.get_waiter("function_active_v2").wait(FunctionName=function_name)
    return response["FunctionArn"], True


def allow_s3_invoke(lambda_client, function_name, bucket, account_id):
    """Allow S3 to call the function for events of this bucket. Returns False if already allowed.

    SourceAccount makes sure a bucket with the same name in another account can't call it.
    """
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=f"s3-invoke-{bucket.replace('.', '-')}",
            Action="lambda:InvokeFunction",
            Principal="s3.amazonaws.com",
            SourceArn=f"arn:aws:s3:::{bucket}",
            SourceAccount=account_id,
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceConflictException":
            return False
        raise


def read_notification_configuration(client, bucket):
    """Return the bucket's event notifications without the response metadata."""
    response = client.get_bucket_notification_configuration(Bucket=bucket)
    return {key: response[key] for key in NOTIFICATION_KEYS if key in response}


def without_detection_triggers(configuration):
    lambdas = configuration.get("LambdaFunctionConfigurations", [])
    return [item for item in lambdas if not item.get("Id", "").startswith(TRIGGER_ID_PREFIX)]


def set_detection_trigger(client, bucket, function_arn):
    """Call the function for every new image in the bucket.

    One configuration per extension, because a filter can only have one suffix. Only images
    trigger it, so the .json results it writes into the same bucket never call it again.
    Other notifications of the bucket are kept (put_bucket_notification_configuration replaces all).
    """
    configuration = read_notification_configuration(client, bucket)
    lambdas = without_detection_triggers(configuration)
    for extension in TRIGGER_EXTENSIONS:
        lambdas.append(
            {
                "Id": f"{TRIGGER_ID_PREFIX}{extension[1:]}",
                "LambdaFunctionArn": function_arn,
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {"Key": {"FilterRules": [{"Name": "suffix", "Value": extension}]}},
            }
        )
    configuration["LambdaFunctionConfigurations"] = lambdas
    logger.info("Connecting s3://%s to %s...", bucket, function_arn)
    client.put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration=configuration)


def remove_detection_trigger(client, bucket):
    """Remove the triggers added by set_detection_trigger. Returns how many were removed."""
    configuration = read_notification_configuration(client, bucket)
    lambdas = without_detection_triggers(configuration)
    removed = len(configuration.get("LambdaFunctionConfigurations", [])) - len(lambdas)
    if removed:
        configuration["LambdaFunctionConfigurations"] = lambdas
        client.put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration=configuration)
    return removed


def deploy_object_detection(
    s3_client, lambda_client, sts_client, bucket, provider, api_key, role_arn=None, function_name=DEFAULT_FUNCTION_NAME
):
    """Deploy the function and connect it to the bucket. Returns (function ARN, True if created).

    The Lambda client must be in the bucket's region: S3 can only call functions in its own region.
    """
    account_id = get_account_id(sts_client)
    arn, created = deploy_detection_lambda(
        lambda_client,
        build_lambda_zip(),
        role_arn or default_role_arn(account_id),
        function_name,
        detection_environment(provider, api_key),
    )
    # S3 checks that it may call the function when the trigger is saved, so permission first.
    allow_s3_invoke(lambda_client, function_name, bucket, account_id)
    set_detection_trigger(s3_client, bucket, arn)
    return arn, created


def remove_object_detection(s3_client, lambda_client, bucket, function_name=DEFAULT_FUNCTION_NAME):
    """Remove the bucket triggers and delete the function. Returns (removed triggers, deleted)."""
    removed = remove_detection_trigger(s3_client, bucket)
    if not function_exists(lambda_client, function_name):
        return removed, False
    logger.info("Deleting Lambda function '%s'...", function_name)
    lambda_client.delete_function(FunctionName=function_name)
    return removed, True


def result_key(key):
    """Return the key of the JSON result: photos/cat.jpg -> photos/cat.json (same as the function)."""
    return f"{key.rsplit('.', 1)[0]}.json" if "." in key else f"{key}.json"


def check_image_key(key):
    """Raise ValueError if the trigger would not fire for this key."""
    if not key.lower().endswith(IMAGE_EXTENSIONS):
        raise ValueError(f"Only {', '.join(IMAGE_EXTENSIONS)} images trigger the detection, got '{key}'.")


def wait_for_result(client, bucket, key, timeout=60, interval=2, sleep=time.sleep, clock=time.monotonic):
    """Wait until the JSON result appears in the bucket and return it parsed."""
    deadline = clock() + timeout
    while objects.get_object_info(client, bucket, key) is None:
        if clock() >= deadline:
            raise TimeoutError(
                f"No result at s3://{bucket}/{key} after {timeout} seconds. "
                "Check the function's logs in CloudWatch (Lambda > Monitor > View CloudWatch logs)."
            )
        sleep(interval)
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def detect_image(client, path, bucket, key=None, timeout=60):
    """Upload an image, let the Lambda function detect its objects and return (key, result)."""
    key = key or Path(path).name
    check_image_key(key)
    output_key = result_key(key)
    # An old result of the same image would be read at once, so delete it first.
    if objects.get_object_info(client, bucket, output_key) is not None:
        objects.delete_object(client, bucket, output_key)

    key, _ = objects.upload_file(client, path, bucket, key=key, method="small", validate_mime=True)
    logger.info("Waiting for s3://%s/%s...", bucket, output_key)
    return key, wait_for_result(client, bucket, output_key, timeout=timeout)


def query_detection_api(image_bytes, provider, api_key):
    """Call the detection API directly from this computer (no AWS), like the function does."""
    if provider == "huggingface":
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "image/jpeg"}
        response = requests.post(HF_API_URL, headers=headers, data=image_bytes, timeout=30)
    else:
        response = requests.post(
            API_NINJAS_URL, headers={"X-Api-Key": api_key}, files={"image": image_bytes}, timeout=30
        )
    if response.status_code != 200:
        raise ValueError(f"{provider} answered {response.status_code}: {response.text}")
    return response.json()


def normalize_detections(result, min_confidence=0.0):
    """Turn both API answers into [{"label", "confidence", "box": (x1, y1, x2, y2)}], best first.

    API Ninjas: {"label", "confidence": "0.97" (a string), "bounding_box": {"x1", "y1", "x2", "y2"}}
    Hugging Face: {"label", "score": 0.97, "box": {"xmin", "ymin", "xmax", "ymax"}}
    The function stores {"error": ...} when the API failed; that is raised as ValueError.
    """
    if isinstance(result, dict):
        raise ValueError(f"Detection failed: {result.get('error', result)}")

    detections = []
    for item in result:
        if "bounding_box" in item:
            box = item["bounding_box"]
            corners = (box["x1"], box["y1"], box["x2"], box["y2"])
            confidence = float(item["confidence"])
        else:
            box = item["box"]
            corners = (box["xmin"], box["ymin"], box["xmax"], box["ymax"])
            confidence = float(item["score"])
        if confidence >= min_confidence:
            detections.append(
                {"label": item["label"], "confidence": confidence, "box": tuple(int(float(v)) for v in corners)}
            )
    return sorted(detections, key=lambda detection: detection["confidence"], reverse=True)
