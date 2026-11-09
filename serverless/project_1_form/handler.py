"""Lecture 6, Project 1: the Lambda functions behind the server-less form.

lambda_handler (POST /) saves the form to DynamoDB, show_form (GET /) returns the form page.
Deployed by serverless.yml on its own, so only boto3 (already in the Lambda runtime) is used.
"""

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

DYNAMODB_TABLE = os.environ.get("DYNAMO_DB_TABLE", "")
STATIC_DIR = Path(__file__).resolve().parent / "static-web"

FORM_FIELDS = ("name", "email", "phone", "address", "programming_languages", "tools")

CONFIG_SCRIPT_TAG = '<script src="js/config.js"></script>'
FORM_SCRIPT_TAG = '<script src="js/form.js"></script>'

dynamodb = boto3.client("dynamodb")


def response(status_code, body, content_type="application/json"):
    """Build the reply API Gateway expects from a Lambda proxy integration."""
    if not isinstance(body, str):
        body = json.dumps(body)
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": content_type,
            "Access-Control-Allow-Origin": "*",
        },
        "body": body,
    }


def read_body(event):
    """Return the JSON body of the request as a dict, or None if it is not a JSON object."""
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def missing_fields(data):
    """Names of the form fields that are missing, empty or not text."""
    return [
        field
        for field in FORM_FIELDS
        if not isinstance(data.get(field), str) or not data[field].strip()
    ]


def save_to_dynamodb(data):
    """Save one form as a new DynamoDB item and return its id."""
    item_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    item = {"id": {"S": item_id}}
    for field in FORM_FIELDS:
        item[field] = {"S": data[field].strip()}
    item["createdAt"] = {"S": timestamp}
    item["updatedAt"] = {"S": timestamp}

    dynamodb.put_item(TableName=DYNAMODB_TABLE, Item=item)
    return item_id


def lambda_handler(event, context):
    """POST / : validate the form and save it to DynamoDB."""
    data = read_body(event)
    if data is None:
        return response(400, {"message": "The request body must be a JSON object"})

    missing = missing_fields(data)
    if missing:
        return response(400, {"message": "Missing or empty fields", "fields": missing})

    try:
        item_id = save_to_dynamodb(data)
    except ClientError as error:
        print(f"DynamoDB error: {error.response['Error']['Message']}")
        return response(500, {"message": "Could not save the form"})

    print(f"Saved form {item_id} to {DYNAMODB_TABLE}")
    return response(200, {"message": "Data received Successfully", "id": item_id})


def api_url(event):
    """The URL of this API stage, for example https://abc123.execute-api.us-east-1.amazonaws.com/prod/"""
    request_context = event.get("requestContext") or {}
    domain = request_context.get("domainName")
    stage = request_context.get("stage")
    if not domain or not stage:
        return ""
    return f"https://{domain}/{stage}/"


def show_form(event, context):
    """GET / : return the form page, with the API URL and form.js written into the HTML.

    The page then posts to the same API it was loaded from, so nothing has to be edited after
    `serverless deploy`.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    form_js = (STATIC_DIR / "js" / "form.js").read_text(encoding="utf-8")

    config = f"<script>window.FORM_API_URL = {json.dumps(api_url(event))};</script>"
    html = html.replace(CONFIG_SCRIPT_TAG, config)
    html = html.replace(FORM_SCRIPT_TAG, f"<script>\n{form_js}\n</script>")
    return response(200, html, content_type="text/html; charset=utf-8")
