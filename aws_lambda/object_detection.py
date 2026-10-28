"""AWS Lambda function: detect objects on images uploaded to S3 and save the result next to them.

S3 calls lambda_handler for every new .jpg / .jpeg / .png (the trigger set up by
`s3-manager detect deploy`). The image is sent to an object detection API and the answer is
stored as JSON with the same name: photos/cat.jpg -> photos/cat.json.

This file is deployed on its own, so it only uses what the Lambda Python runtime already has:
the standard library, boto3 and urllib3 (no extra layer needed).

Environment variables of the function:
    PROVIDER        - "api-ninjas" (default, free, no bank card) or "huggingface"
    API_NINJAS_KEY  - key from https://api-ninjas.com (for PROVIDER=api-ninjas)
    HF_API_TOKEN    - Hugging Face token (for PROVIDER=huggingface)
"""

import io
import json
import logging
import os
from urllib.parse import unquote_plus

import boto3
import urllib3

PROVIDER = os.environ.get("PROVIDER", "api-ninjas")

API_NINJAS_KEY = os.environ.get("API_NINJAS_KEY", "")
API_NINJAS_URL = "https://api.api-ninjas.com/v1/objectdetection"

HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")
HF_API_URL = "https://router.huggingface.co/hf-inference/models/facebook/detr-resnet-50"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
REQUEST_TIMEOUT = 20  # seconds; the function itself has a 30 second timeout

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Created once per Lambda container and reused by every invocation.
s3_client = boto3.client("s3")
http = urllib3.PoolManager()


def read_api_response(response):
    """Return the parsed JSON answer, or {"error", "status"} if the API did not answer 200."""
    text = response.data.decode("utf-8")
    if response.status != 200:
        logger.error("Detection API answered %s: %s", response.status, text)
        return {"error": text, "status": response.status}
    return json.loads(text)


def query_api_ninjas(file):
    """Send an image to API Ninjas Object Detection.

    Answer: [{"label": "dog", "confidence": "0.97", "bounding_box": {"x1", "y1", "x2", "y2"}}, ...]
    """
    if not API_NINJAS_KEY:
        return {"error": "API_NINJAS_KEY is not set in the Lambda environment variables."}
    image_bytes = file.read() if hasattr(file, "read") else file
    response = http.request(
        "POST",
        API_NINJAS_URL,
        headers={"X-Api-Key": API_NINJAS_KEY},
        fields={"image": ("image.jpg", image_bytes)},
        timeout=REQUEST_TIMEOUT,
    )
    return read_api_response(response)


def query_huggingface(file):
    """Send an image to the facebook/detr-resnet-50 model on Hugging Face.

    Answer: [{"label": "dog", "score": 0.97, "box": {"xmin", "ymin", "xmax", "ymax"}}, ...]
    """
    if not HF_API_TOKEN:
        return {"error": "HF_API_TOKEN is not set in the Lambda environment variables."}
    image_bytes = file.read() if hasattr(file, "read") else file
    headers = {"Authorization": f"Bearer {HF_API_TOKEN}", "Content-Type": "image/jpeg"}
    response = http.request("POST", HF_API_URL, headers=headers, body=image_bytes, timeout=REQUEST_TIMEOUT)
    return read_api_response(response)


def query_image(file, provider=None):
    """Detect objects on an image with the chosen provider (default: the PROVIDER variable)."""
    if (provider or PROVIDER) == "huggingface":
        return query_huggingface(file)
    return query_api_ninjas(file)


def result_key(key):
    """Return the key of the JSON result: photos/cat.jpg -> photos/cat.json."""
    return f"{key.rsplit('.', 1)[0]}.json" if "." in key else f"{key}.json"


def lambda_handler(event, _context):
    """Handle an S3 "object created" event: detect objects on each new image, save JSON next to it."""
    saved = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # Keys in S3 events are URL-encoded: "my photo.jpg" arrives as "my+photo.jpg".
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.lower().endswith(IMAGE_EXTENSIONS):
            logger.info("Skipping s3://%s/%s, not an image.", bucket, key)
            continue

        logger.info("Detecting objects on s3://%s/%s with %s...", bucket, key, PROVIDER)
        file = io.BytesIO()
        s3_client.download_fileobj(Bucket=bucket, Key=key, Fileobj=file)
        file.seek(0)

        result = query_image(file)

        output_key = result_key(key)
        s3_client.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=json.dumps(result, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Saved the result to s3://%s/%s.", bucket, output_key)
        saved.append(output_key)

    return {"statusCode": 200, "body": json.dumps({"results": saved})}
