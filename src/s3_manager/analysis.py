"""Image analysis with AWS Lambda and four API Ninjas endpoints: deploy, run and read the results.

The function itself is aws_lambda/image_analysis.py. After `deploy_image_analysis` every new
.jpg / .jpeg / .png in the bucket triggers it, and it saves one JSON answer per API type:
sample.jpg -> json/classification_sample.json, json/objectdetection_sample.json,
json/celebrity_sample.json, json/ocr_sample.json.

Deploying reuses the helpers of detection.py (like organizer.py does); the handler file, the
timeout, the settings and the trigger ids differ. Use a bucket without the detect or organize
trigger: S3 refuses two triggers whose filters overlap.
"""

import json
import logging
import re
from pathlib import Path

import requests

from s3_manager import detection, objects, organizer

logger = logging.getLogger(__name__)

HANDLER_FILE = Path(__file__).resolve().parents[2] / "aws_lambda" / "image_analysis.py"

DEFAULT_FUNCTION_NAME = "image-analysis"
LAMBDA_HANDLER = "image_analysis.lambda_handler"  # <file name without .py>.<function name>
LAMBDA_TIMEOUT = 60  # seconds; four API calls in parallel plus a cold start
TRIGGER_ID_PREFIX = "image-analysis-"

# The same names and URLs as in the Lambda function.
API_NINJAS_BASE_URL = "https://api.api-ninjas.com/v1"
API_URLS = {
    "classification": f"{API_NINJAS_BASE_URL}/imageclassification",
    "objectdetection": f"{API_NINJAS_BASE_URL}/objectdetection",
    "celebrity": f"{API_NINJAS_BASE_URL}/celebrity",
    "ocr": f"{API_NINJAS_BASE_URL}/imagetotext",
}
API_TYPES = tuple(API_URLS)
DEFAULT_API_TYPES = ",".join(API_TYPES)
RESULT_PREFIX = "json/"


def parse_api_types(text):
    """Turn "classification, ocr" into ["classification", "ocr"]. Raises ValueError for unknown names."""
    api_types = []
    for name in text.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name not in API_URLS:
            raise ValueError(f"Unknown API type '{name}'. Use one or more of: {', '.join(API_TYPES)}.")
        if name not in api_types:
            api_types.append(name)
    if not api_types:
        raise ValueError(f"Choose at least one API type: {', '.join(API_TYPES)}.")
    return api_types


def analysis_environment(api_key, api_types, result_bucket=None):
    """Return the environment variables for the function."""
    environment = {"API_NINJAS_KEY": api_key, "API_TYPES": ",".join(api_types)}
    if result_bucket:
        environment["RESULT_BUCKET"] = result_bucket
    return environment


def result_key(api_type, key):
    """Return the key of one JSON result (same rule as the function).

    sample.jpg -> json/classification_sample.json, holiday/cat.jpg -> json/holiday/classification_cat.json
    """
    folder, _, name = key.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    prefix = f"{RESULT_PREFIX}{folder}/" if folder else RESULT_PREFIX
    return f"{prefix}{api_type}_{stem}.json"


def result_keys(key, api_types):
    """Return {api_type: result key} for one image."""
    return {api_type: result_key(api_type, key) for api_type in api_types}


def celebrity_name(key):
    """Return the name the celebrity API is searched with: michael_jordan.jpg -> "michael jordan"."""
    name = key.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return " ".join(part for part in re.split(r"[\s_\-]+", stem) if part)


def check_free_bucket(configuration, bucket):
    """Raise ValueError if the bucket runs the detect or organize function (see the module docstring)."""
    for item in configuration.get("LambdaFunctionConfigurations", []):
        trigger_id = item.get("Id", "")
        if trigger_id.startswith(detection.TRIGGER_ID_PREFIX):
            raise ValueError(
                f"Bucket '{bucket}' already runs the object detection function. "
                "Use another bucket, or remove it first with 'detect delete'."
            )
        if trigger_id == organizer.TRIGGER_ID:
            raise ValueError(
                f"Bucket '{bucket}' already runs the organize function. "
                "Use another bucket, or remove it first with 'organize delete'."
            )


def deploy_image_analysis(
    s3_client,
    lambda_client,
    sts_client,
    bucket,
    api_key,
    api_types=API_TYPES,
    result_bucket=None,
    role_arn=None,
    function_name=DEFAULT_FUNCTION_NAME,
):
    """Deploy the function and connect it to the bucket. Returns (function ARN, True if created).

    The Lambda client must be in the bucket's region: S3 can only call functions in its own region.
    The bucket is checked first, so a bucket with another trigger fails before anything changes.
    """
    check_free_bucket(detection.read_notification_configuration(s3_client, bucket), bucket)
    account_id = detection.get_account_id(sts_client)
    arn, created = detection.deploy_detection_lambda(
        lambda_client,
        detection.build_lambda_zip(HANDLER_FILE),
        role_arn or detection.default_role_arn(account_id),
        function_name,
        analysis_environment(api_key, api_types, result_bucket),
        handler=LAMBDA_HANDLER,
        description="Sends new images to API Ninjas (classification, objects, celebrity, OCR) (s3-manager).",
        timeout=LAMBDA_TIMEOUT,
    )
    # S3 checks that it may call the function when the trigger is saved, so permission first.
    detection.allow_s3_invoke(lambda_client, function_name, bucket, account_id)
    detection.set_detection_trigger(s3_client, bucket, arn, prefix=TRIGGER_ID_PREFIX)
    return arn, created


def remove_image_analysis(s3_client, lambda_client, bucket, function_name=DEFAULT_FUNCTION_NAME):
    """Remove the bucket triggers and delete the function. Returns (removed triggers, deleted)."""
    removed = detection.remove_detection_trigger(s3_client, bucket, prefix=TRIGGER_ID_PREFIX)
    if not detection.function_exists(lambda_client, function_name):
        return removed, False
    logger.info("Deleting Lambda function '%s'...", function_name)
    lambda_client.delete_function(FunctionName=function_name)
    return removed, True


def read_function_settings(lambda_client, function_name=DEFAULT_FUNCTION_NAME):
    """Return (API types, result bucket or None) of the deployed function.

    `analyze image` waits for exactly the files the function writes, even if it was deployed
    with --apis or --result-bucket.
    """
    configuration = lambda_client.get_function_configuration(FunctionName=function_name)
    variables = configuration.get("Environment", {}).get("Variables", {})
    api_types = parse_api_types(variables.get("API_TYPES") or DEFAULT_API_TYPES)
    return api_types, variables.get("RESULT_BUCKET") or None


def analyze_image(client, path, bucket, api_types=API_TYPES, key=None, result_bucket=None, timeout=90):
    """Upload an image, let the Lambda function analyse it and return (key, {api_type: result})."""
    key = key or Path(path).name
    detection.check_image_key(key)
    target_bucket = result_bucket or bucket
    keys = result_keys(key, api_types)
    # Old results of the same image would be read at once, so delete them first.
    for output_key in keys.values():
        if objects.get_object_info(client, target_bucket, output_key) is not None:
            objects.delete_object(client, target_bucket, output_key)

    key, _ = objects.upload_file(client, path, bucket, key=key, method="small", validate_mime=True)
    results = {}
    for api_type, output_key in keys.items():
        logger.info("Waiting for s3://%s/%s...", target_bucket, output_key)
        results[api_type] = detection.wait_for_result(client, target_bucket, output_key, timeout=timeout)
    return key, results


def query_analysis_api(api_type, image_bytes, filename, api_key):
    """Call one API type directly from this computer (no AWS), like the function does.

    Errors are returned as {"error", "status"} (like in the function), so the other APIs still run.
    """
    headers = {"X-Api-Key": api_key}
    if api_type == "celebrity":
        response = requests.get(
            API_URLS["celebrity"], headers=headers, params={"name": celebrity_name(filename)}, timeout=30
        )
    else:
        response = requests.post(
            API_URLS[api_type], headers=headers, files={"image": (filename, image_bytes)}, timeout=30
        )
    if response.status_code != 200:
        return {"error": response.text, "status": response.status_code}
    return response.json()


def analyze_local(path, api_types, api_key):
    """Send a local image to every API type and return {api_type: result}."""
    path = Path(path)
    image_bytes = path.read_bytes()
    return {api_type: query_analysis_api(api_type, image_bytes, path.name, api_key) for api_type in api_types}


def save_results(results, key, output_dir):
    """Save each answer under output_dir with the same name as in S3. Returns the written paths."""
    paths = []
    for api_type, result in results.items():
        path = Path(output_dir) / result_key(api_type, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        paths.append(path)
    return paths


def shorten(text, limit=100):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def summarize_result(api_type, result):
    """Return one line about an API answer, for the terminal.

    Errors: "error (404): ...". OCR: the recognised text. Others: the labels or names, with the
    confidence when the API sends one (API Ninjas sends it as a string, e.g. "0.97").
    """
    if isinstance(result, dict) and "error" in result:
        status = f" ({result['status']})" if "status" in result else ""
        return shorten(f"error{status}: {result['error']}")
    if not isinstance(result, list):
        return shorten(json.dumps(result))
    if not result:
        return "nothing found"
    if api_type == "ocr":
        return shorten(" ".join(str(item.get("text", "")) for item in result))

    parts = []
    for item in result[:5]:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        label = item.get("label") or item.get("name") or item.get("text") or "?"
        try:
            parts.append(f"{label} {float(item['confidence']):.2f}")
        except (KeyError, TypeError, ValueError):
            parts.append(str(label))
    more = f" (+{len(result) - 5} more)" if len(result) > 5 else ""
    return shorten(", ".join(parts)) + more
