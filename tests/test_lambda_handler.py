import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from s3_manager.detection import HANDLER_FILE


@pytest.fixture
def handler(monkeypatch):
    """The Lambda function module, loaded from its file, with a fake S3 client and HTTP pool."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("object_detection", HANDLER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.s3_client = MagicMock()
    module.http = MagicMock()
    module.API_NINJAS_KEY = "test-key"
    module.http.request.return_value = api_response(200, [{"label": "cat", "confidence": "0.9"}])
    return module


def api_response(status, body):
    return SimpleNamespace(status=status, data=json.dumps(body).encode("utf-8"))


def s3_event(key, bucket="my-photos"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def saved_result(handler):
    return json.loads(handler.s3_client.put_object.call_args.kwargs["Body"])


def test_handler_decodes_key_and_saves_json_next_to_image(handler):
    # S3 sends "holiday/my cat.JPG" as "holiday/my+cat.JPG".
    response = handler.lambda_handler(s3_event("holiday/my+cat.JPG"), None)

    assert handler.s3_client.download_fileobj.call_args.kwargs["Key"] == "holiday/my cat.JPG"
    kwargs = handler.s3_client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "my-photos"
    assert kwargs["Key"] == "holiday/my cat.json"
    assert kwargs["ContentType"] == "application/json"
    assert saved_result(handler) == [{"label": "cat", "confidence": "0.9"}]
    assert json.loads(response["body"]) == {"results": ["holiday/my cat.json"]}


@pytest.mark.parametrize("key", ["cat.json", "notes.txt", "README"])
def test_handler_skips_files_that_are_not_images(handler, key):
    handler.lambda_handler(s3_event(key), None)

    handler.s3_client.download_fileobj.assert_not_called()
    handler.s3_client.put_object.assert_not_called()


def test_api_ninjas_request_sends_key_and_image(handler):
    handler.query_api_ninjas(b"image bytes")

    method, url = handler.http.request.call_args.args
    kwargs = handler.http.request.call_args.kwargs
    assert (method, url) == ("POST", handler.API_NINJAS_URL)
    assert kwargs["headers"] == {"X-Api-Key": "test-key"}
    assert kwargs["fields"] == {"image": ("image.jpg", b"image bytes")}


def test_missing_api_key_is_saved_as_error_without_request(handler):
    handler.API_NINJAS_KEY = ""

    handler.lambda_handler(s3_event("cat.jpg"), None)

    handler.http.request.assert_not_called()
    assert "API_NINJAS_KEY is not set" in saved_result(handler)["error"]


def test_api_error_is_saved_with_status(handler):
    handler.http.request.return_value = api_response(401, {"error": "Invalid API Key."})

    handler.lambda_handler(s3_event("cat.png"), None)

    assert saved_result(handler) == {"error": '{"error": "Invalid API Key."}', "status": 401}


def test_huggingface_provider_uses_bearer_token(handler):
    handler.HF_API_TOKEN = "hf_test"

    handler.query_image(b"image bytes", provider="huggingface")

    method, url = handler.http.request.call_args.args
    assert url == handler.HF_API_URL
    assert handler.http.request.call_args.kwargs["headers"]["Authorization"] == "Bearer hf_test"


@pytest.mark.parametrize(
    ("key", "expected"),
    [("cat.jpg", "cat.json"), ("photos/cat.v2.png", "photos/cat.v2.json"), ("photo", "photo.json")],
)
def test_result_key(handler, key, expected):
    assert handler.result_key(key) == expected
