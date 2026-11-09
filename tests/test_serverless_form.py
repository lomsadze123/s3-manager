"""Lecture 6, Project 1: the Lambda functions of serverless/project_1_form (no AWS needed)."""

import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import make_client_error

PROJECT_DIR = Path(__file__).resolve().parents[1] / "serverless" / "project_1_form"
HANDLER_FILE = PROJECT_DIR / "handler.py"

FORM = {
    "name": "Beka Lomsadze",
    "email": "beka@example.com",
    "phone": "+995 555 00 00 00",
    "address": "Tbilisi, Georgia",
    "programming_languages": "Python, JavaScript",
    "tools": "AWS, Docker",
}


@pytest.fixture
def handler(monkeypatch):
    """The Lambda module, loaded from its file, with a fake DynamoDB client."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DYNAMO_DB_TABLE", "serverlessFORM-prod")
    spec = importlib.util.spec_from_file_location("serverless_form_handler", HANDLER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.dynamodb = MagicMock()
    return module


def post_event(body):
    return {"httpMethod": "POST", "body": body if isinstance(body, str) else json.dumps(body)}


def get_event(domain="abc123.execute-api.us-east-1.amazonaws.com", stage="prod"):
    return {"httpMethod": "GET", "body": None, "requestContext": {"domainName": domain, "stage": stage}}


def test_valid_form_is_saved_to_dynamodb(handler):
    response = handler.lambda_handler(post_event(FORM), None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["message"] == "Data received Successfully"

    call = handler.dynamodb.put_item.call_args
    assert call.kwargs["TableName"] == "serverlessFORM-prod"
    item = call.kwargs["Item"]
    assert item["id"] == {"S": body["id"]}
    for field, value in FORM.items():
        assert item[field] == {"S": value}
    assert item["createdAt"] == item["updatedAt"]
    assert item["createdAt"]["S"].endswith("+00:00")


def test_every_response_allows_any_origin(handler):
    ok = handler.lambda_handler(post_event(FORM), None)
    bad = handler.lambda_handler(post_event({}), None)

    for response in (ok, bad):
        assert response["headers"]["Access-Control-Allow-Origin"] == "*"
        assert response["headers"]["Content-Type"] == "application/json"


def test_values_are_trimmed(handler):
    handler.lambda_handler(post_event({**FORM, "name": "  Beka  "}), None)

    assert handler.dynamodb.put_item.call_args.kwargs["Item"]["name"] == {"S": "Beka"}


def test_missing_and_empty_fields_are_rejected(handler):
    form = {**FORM, "email": "   ", "tools": 5}
    del form["phone"]

    response = handler.lambda_handler(post_event(form), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["fields"] == ["email", "phone", "tools"]
    handler.dynamodb.put_item.assert_not_called()


@pytest.mark.parametrize("body", ["not json", "[1, 2]", "", None])
def test_body_that_is_not_a_json_object_is_rejected(handler, body):
    response = handler.lambda_handler({"body": body}, None)

    assert response["statusCode"] == 400
    handler.dynamodb.put_item.assert_not_called()


def test_base64_body_is_decoded(handler):
    encoded = base64.b64encode(json.dumps(FORM).encode("utf-8")).decode("ascii")

    response = handler.lambda_handler({"body": encoded, "isBase64Encoded": True}, None)

    assert response["statusCode"] == 200


def test_dynamodb_error_returns_500(handler):
    handler.dynamodb.put_item.side_effect = make_client_error("ResourceNotFoundException")

    response = handler.lambda_handler(post_event(FORM), None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"message": "Could not save the form"}


def test_show_form_writes_the_api_url_and_form_js_into_the_page(handler):
    response = handler.show_form(get_event(), None)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "text/html; charset=utf-8"
    html = response["body"]
    assert 'window.FORM_API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com/prod/";' in html
    assert 'src="js/config.js"' not in html
    assert 'src="js/form.js"' not in html
    form_js = (PROJECT_DIR / "static-web" / "js" / "form.js").read_text(encoding="utf-8")
    assert form_js in html
    assert 'id="mainForm"' in html


def test_api_url_is_empty_without_request_context(handler):
    assert handler.api_url({}) == ""
    assert handler.api_url(get_event(stage="dev")) == "https://abc123.execute-api.us-east-1.amazonaws.com/dev/"


def test_page_and_handler_use_the_same_fields(handler):
    html = (PROJECT_DIR / "static-web" / "index.html").read_text(encoding="utf-8")
    form_js = (PROJECT_DIR / "static-web" / "js" / "form.js").read_text(encoding="utf-8")

    for field in handler.FORM_FIELDS:
        assert f'id="{field}"' in html
        # either quote style, so a formatter (Prettier) can't break the test
        assert f'"{field}"' in form_js or f"'{field}'" in form_js
    assert handler.CONFIG_SCRIPT_TAG in html
    assert handler.FORM_SCRIPT_TAG in html
