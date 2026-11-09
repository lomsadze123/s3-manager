"""AWS Lambda function: analyse images uploaded to S3 with four API Ninjas endpoints.

This is the lecture's lambda_1.py with Hugging Face replaced by API Ninjas only. S3 calls
lambda_handler for every new .jpg / .jpeg / .png (the trigger set up by `s3-manager analyze deploy`,
event s3:ObjectCreated:*). The image is read from S3 as bytes, sent to every API type below, and
each JSON answer is saved on its own as json/{api_type}_{image_name}.json:

    sample.jpg -> json/classification_sample.json
                  json/objectdetection_sample.json
                  json/celebrity_sample.json
                  json/ocr_sample.json

API types:
    classification   POST the image to /v1/imageclassification
    objectdetection  POST the image to /v1/objectdetection
    celebrity        GET /v1/celebrity?name=...  The Celebrity API only searches by name, it does
                     not take an image, so the name comes from the file name:
                     michael_jordan.jpg -> "michael jordan"
    ocr              POST the image to /v1/imagetotext (text recognition)

If one endpoint fails, its file gets {"error": ..., "status": ...} and the others are still saved.

This file is deployed on its own, so it only uses what the Lambda Python runtime already has:
the standard library, boto3 and urllib3 (no extra layer needed).

Environment variables of the function:
    API_NINJAS_KEY  - key from https://api-ninjas.com (free, no bank card)
    API_TYPES       - comma-separated API types to call (default: all four above)
    RESULT_BUCKET   - bucket for the JSON results (default: the bucket of the image)
"""

import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote_plus

import boto3
import urllib3

API_NINJAS_KEY = os.environ.get("API_NINJAS_KEY", "")
API_TYPES = os.environ.get("API_TYPES", "classification,objectdetection,celebrity,ocr")
RESULT_BUCKET = os.environ.get("RESULT_BUCKET", "")

API_NINJAS_BASE_URL = "https://api.api-ninjas.com/v1"
APIS = {
    "classification": f"{API_NINJAS_BASE_URL}/imageclassification",
    "objectdetection": f"{API_NINJAS_BASE_URL}/objectdetection",
    "celebrity": f"{API_NINJAS_BASE_URL}/celebrity",
    "ocr": f"{API_NINJAS_BASE_URL}/imagetotext",
}

RESULT_PREFIX = "json/"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
REQUEST_TIMEOUT = 20  # seconds per request; the calls run in parallel, the function has 60 seconds

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Created once per Lambda container and reused by every invocation (PoolManager is thread-safe).
s3_client = boto3.client("s3")
http = urllib3.PoolManager()


def read_api_response(response):
    """Return the parsed JSON answer, or {"error", "status"} if the API did not answer 200."""
    text = response.data.decode("utf-8")
    if response.status != 200:
        logger.error("API Ninjas answered %s: %s", response.status, text)
        return {"error": text, "status": response.status}
    return json.loads(text)


def query_api_ninjas(url, image_bytes, filename):
    """POST an image to an API Ninjas endpoint as the multipart field "image".

    The real file name is sent so urllib3 can set the right Content-Type (image/jpeg or image/png).
    """
    response = http.request(
        "POST",
        url,
        headers={"X-Api-Key": API_NINJAS_KEY},
        fields={"image": (filename, image_bytes)},
        timeout=REQUEST_TIMEOUT,
    )
    return read_api_response(response)


def celebrity_name(key):
    """Return the name to search for, from the file name: photos/michael_jordan.jpg -> "michael jordan"."""
    name = key.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return " ".join(part for part in re.split(r"[\s_\-]+", stem) if part)


def query_celebrity(key):
    """Search the Celebrity API by the name in the file name (it has no image input)."""
    response = http.request(
        "GET",
        APIS["celebrity"],
        headers={"X-Api-Key": API_NINJAS_KEY},
        fields={"name": celebrity_name(key)},
        timeout=REQUEST_TIMEOUT,
    )
    return read_api_response(response)


def query_api(api_type, image_bytes, key):
    """Call one API type for the image and return its JSON answer (or an error dict)."""
    if not API_NINJAS_KEY:
        return {"error": "API_NINJAS_KEY is not set in the Lambda environment variables."}
    if api_type == "celebrity":
        return query_celebrity(key)
    return query_api_ninjas(APIS[api_type], image_bytes, key.rsplit("/", 1)[-1])


def selected_api_types(setting):
    """Turn "classification, ocr" into ["classification", "ocr"]. Unknown names are skipped."""
    api_types = []
    for name in setting.split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name not in APIS:
            logger.warning("Unknown API type '%s', skipping it (known: %s).", name, ", ".join(APIS))
        elif name not in api_types:
            api_types.append(name)
    return api_types


def result_key(api_type, key):
    """Return the key of one JSON result: sample.jpg -> json/classification_sample.json.

    An image in a folder keeps its folder, so two cat.jpg files don't overwrite each other:
    holiday/cat.jpg -> json/holiday/classification_cat.json
    """
    folder, _, name = key.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    prefix = f"{RESULT_PREFIX}{folder}/" if folder else RESULT_PREFIX
    return f"{prefix}{api_type}_{stem}.json"


def read_image(bucket, key):
    """Download the image from S3 into memory and return its bytes."""
    file = io.BytesIO()
    s3_client.download_fileobj(Bucket=bucket, Key=key, Fileobj=file)
    return file.getvalue()


def analyze_image(image_bytes, key, api_types):
    """Call all API types at the same time and return {api_type: answer}.

    In parallel the whole analysis takes about as long as the slowest API. An exception (timeout,
    not JSON) is stored as an error for that API only.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max(len(api_types), 1)) as pool:
        futures = {api_type: pool.submit(query_api, api_type, image_bytes, key) for api_type in api_types}
        for api_type, future in futures.items():
            try:
                results[api_type] = future.result()
            except Exception as error:  # one failing API must not lose the other results
                logger.exception("%s failed for %s", api_type, key)
                results[api_type] = {"error": f"{type(error).__name__}: {error}"}
    return results


def save_result(bucket, key, result):
    """Save one API answer as JSON."""
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(result, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Saved s3://%s/%s.", bucket, key)


def lambda_handler(event, _context):
    """Handle an S3 "object created" event: analyse each new image, save one JSON per API type."""
    api_types = selected_api_types(API_TYPES)
    saved = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # Keys in S3 events are URL-encoded: "my photo.jpg" arrives as "my+photo.jpg".
        key = unquote_plus(record["s3"]["object"]["key"])

        if not key.lower().endswith(IMAGE_EXTENSIONS) or key.startswith(RESULT_PREFIX):
            logger.info("Skipping s3://%s/%s, not a new image.", bucket, key)
            continue

        logger.info("Analysing s3://%s/%s with %s...", bucket, key, ", ".join(api_types))
        image_bytes = read_image(bucket, key)
        results = analyze_image(image_bytes, key, api_types)

        target_bucket = RESULT_BUCKET or bucket
        for api_type, result in results.items():
            output_key = result_key(api_type, key)
            save_result(target_bucket, output_key, result)
            saved.append(output_key)

    return {"statusCode": 200, "body": json.dumps({"results": saved})}
