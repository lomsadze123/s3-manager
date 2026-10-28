"""Organize by extension with AWS Lambda: deploy the function and connect it to a bucket.

The function itself is aws_lambda/organize_by_extension.py. After `deploy_organizer` every new
file in the bucket root is moved into a folder named after its extension (demo.csv -> csv/demo.csv).
Deploying reuses the helpers of detection.py, only the handler file and the trigger differ.

Use a different bucket than the object detection one: S3 refuses two triggers whose filters
overlap, and the organizer would also move the .json results and make the detection run again.
"""

import logging
from pathlib import Path

from s3_manager import detection

logger = logging.getLogger(__name__)

HANDLER_FILE = Path(__file__).resolve().parents[2] / "aws_lambda" / "organize_by_extension.py"

DEFAULT_FUNCTION_NAME = "organize-by-extension"
LAMBDA_HANDLER = "organize_by_extension.lambda_handler"  # <file name without .py>.<function name>
TRIGGER_ID = "organize-by-extension"


def organizer_environment(include_unknown=False):
    """Return the environment variables for the function."""
    return {"MOVE_UNKNOWN": "true" if include_unknown else "false"}


def has_detection_trigger(configuration):
    lambdas = configuration.get("LambdaFunctionConfigurations", [])
    return any(item.get("Id", "").startswith(detection.TRIGGER_ID_PREFIX) for item in lambdas)


def check_no_detection_trigger(configuration, bucket):
    """Raise ValueError if the bucket runs the object detection function (see the module docstring)."""
    if has_detection_trigger(configuration):
        raise ValueError(
            f"Bucket '{bucket}' already runs the object detection function. "
            "Use another bucket, or remove it first with 'detect delete'."
        )


def without_organize_trigger(configuration):
    lambdas = configuration.get("LambdaFunctionConfigurations", [])
    return [item for item in lambdas if item.get("Id") != TRIGGER_ID]


def set_organize_trigger(client, bucket, function_arn):
    """Call the function for every new object in the bucket (no filter).

    Raises ValueError if the bucket has the object detection trigger. Other notifications of the
    bucket are kept (put_bucket_notification_configuration replaces all of them).
    """
    configuration = detection.read_notification_configuration(client, bucket)
    check_no_detection_trigger(configuration, bucket)
    lambdas = without_organize_trigger(configuration)
    lambdas.append({"Id": TRIGGER_ID, "LambdaFunctionArn": function_arn, "Events": ["s3:ObjectCreated:*"]})
    configuration["LambdaFunctionConfigurations"] = lambdas
    logger.info("Connecting s3://%s to %s...", bucket, function_arn)
    client.put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration=configuration)


def remove_organize_trigger(client, bucket):
    """Remove the trigger added by set_organize_trigger. Returns how many were removed (0 or 1)."""
    configuration = detection.read_notification_configuration(client, bucket)
    lambdas = without_organize_trigger(configuration)
    removed = len(configuration.get("LambdaFunctionConfigurations", [])) - len(lambdas)
    if removed:
        configuration["LambdaFunctionConfigurations"] = lambdas
        client.put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration=configuration)
    return removed


def deploy_organizer(
    s3_client,
    lambda_client,
    sts_client,
    bucket,
    include_unknown=False,
    role_arn=None,
    function_name=DEFAULT_FUNCTION_NAME,
):
    """Deploy the function and connect it to the bucket. Returns (function ARN, True if created).

    The Lambda client must be in the bucket's region: S3 can only call functions in its own region.
    The bucket is checked first, so a bucket with the detection trigger fails before anything changes.
    """
    check_no_detection_trigger(detection.read_notification_configuration(s3_client, bucket), bucket)
    account_id = detection.get_account_id(sts_client)
    arn, created = detection.deploy_detection_lambda(
        lambda_client,
        detection.build_lambda_zip(HANDLER_FILE),
        role_arn or detection.default_role_arn(account_id),
        function_name,
        organizer_environment(include_unknown),
        handler=LAMBDA_HANDLER,
        description="Moves new files into a folder named after their extension (s3-manager).",
    )
    # S3 checks that it may call the function when the trigger is saved, so permission first.
    detection.allow_s3_invoke(lambda_client, function_name, bucket, account_id)
    set_organize_trigger(s3_client, bucket, arn)
    return arn, created


def remove_organizer(s3_client, lambda_client, bucket, function_name=DEFAULT_FUNCTION_NAME):
    """Remove the bucket trigger and delete the function. Returns (removed triggers, deleted)."""
    removed = remove_organize_trigger(s3_client, bucket)
    if not detection.function_exists(lambda_client, function_name):
        return removed, False
    logger.info("Deleting Lambda function '%s'...", function_name)
    lambda_client.delete_function(FunctionName=function_name)
    return removed, True
