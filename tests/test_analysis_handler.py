import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from s3_manager.analysis import HANDLER_FILE


def api_response(status, body):
    return SimpleNamespace(status=status, data=json.dumps(body).encode("utf-8"))


ANSWERS = {
    "imageclassification": [{"label": "tabby cat", "confidence": "0.91"}],
    "objectdetection": [{"label": "cat", "confidence": "0.97", "bounding_box": {"x1": 1, "y1": 2, "x2": 3, "y2": 4}}],
    "celebrity": [{"name": "michael jordan", "occupation": ["basketball_player"]}],
    "imagetotext": [{"text": "HELLO", "bounding_box": {"x1": 0, "y1": 0, "x2": 9, "y2": 9}}],
}


def fake_request(method, url, **kwargs):
    """Answer like API Ninjas: each endpoint gives its own JSON."""
    return api_response(200, ANSWERS[url.rsplit("/", 1)[-1]])


@pytest.fixture
def handler(monkeypatch):
    """The Lambda function module, loaded from its file, with a fake S3 client and HTTP pool."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("image_analysis", HANDLER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.s3_client = MagicMock()
    module.http = MagicMock()
    module.http.request.side_effect = fake_request
    module.API_NINJAS_KEY = "test-key"
    return module


def s3_event(key, bucket="my-images"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def saved_files(handler):
    """{key: parsed JSON} of every put_object call."""
    calls = handler.s3_client.put_object.call_args_list
    return {call.kwargs["Key"]: json.loads(call.kwargs["Body"]) for call in calls}


def request_to(handler, endpoint):
    for call in handler.http.request.call_args_list:
        if call.args[1].endswith(f"/{endpoint}"):
            return call
    raise AssertionError(f"No request to {endpoint}")


def test_handler_saves_one_json_per_api_type(handler):
    response = handler.lambda_handler(s3_event("sample.jpg"), None)

    assert saved_files(handler) == {
        "json/classification_sample.json": ANSWERS["imageclassification"],
        "json/objectdetection_sample.json": ANSWERS["objectdetection"],
        "json/celebrity_sample.json": ANSWERS["celebrity"],
        "json/ocr_sample.json": ANSWERS["imagetotext"],
    }
    for call in handler.s3_client.put_object.call_args_list:
        assert call.kwargs["Bucket"] == "my-images"
        assert call.kwargs["ContentType"] == "application/json"
    assert len(json.loads(response["body"])["results"]) == 4


def test_handler_reads_the_decoded_key_from_s3(handler):
    # S3 sends "holiday/my cat.PNG" as "holiday/my+cat.PNG".
    handler.lambda_handler(s3_event("holiday/my+cat.PNG"), None)

    assert handler.s3_client.download_fileobj.call_args.kwargs["Key"] == "holiday/my cat.PNG"
    assert "json/holiday/ocr_my cat.json" in saved_files(handler)


@pytest.mark.parametrize("key", ["sample.json", "notes.txt", "README", "json/old.jpg"])
def test_handler_skips_files_that_are_not_new_images(handler, key):
    handler.lambda_handler(s3_event(key), None)

    handler.s3_client.download_fileobj.assert_not_called()
    handler.s3_client.put_object.assert_not_called()


def test_image_is_posted_as_multipart_field_with_its_file_name(handler):
    handler.query_api("ocr", b"image bytes", "photos/sample.png")

    call = request_to(handler, "imagetotext")
    assert call.args == ("POST", handler.APIS["ocr"])
    assert call.kwargs["headers"] == {"X-Api-Key": "test-key"}
    assert call.kwargs["fields"] == {"image": ("sample.png", b"image bytes")}


def test_celebrity_is_searched_by_the_file_name(handler):
    handler.query_api("celebrity", b"image bytes", "people/michael_jordan.jpg")

    call = request_to(handler, "celebrity")
    assert call.args == ("GET", handler.APIS["celebrity"])
    assert call.kwargs["fields"] == {"name": "michael jordan"}


@pytest.mark.parametrize(
    ("key", "name"),
    [("michael_jordan.jpg", "michael jordan"), ("a/taylor-swift 2.png", "taylor swift 2"), ("sample", "sample")],
)
def test_celebrity_name(handler, key, name):
    assert handler.celebrity_name(key) == name


def test_missing_api_key_is_saved_as_error_without_request(handler):
    handler.API_NINJAS_KEY = ""

    handler.lambda_handler(s3_event("sample.jpg"), None)

    handler.http.request.assert_not_called()
    files = saved_files(handler)
    assert len(files) == 4
    assert all("API_NINJAS_KEY is not set" in result["error"] for result in files.values())


def test_one_failing_api_does_not_stop_the_others(handler):
    def request(method, url, **kwargs):
        if url.endswith("/imageclassification"):
            return api_response(404, {"error": "Not found"})
        if url.endswith("/imagetotext"):
            raise TimeoutError("read timed out")
        return fake_request(method, url, **kwargs)

    handler.http.request.side_effect = request

    handler.lambda_handler(s3_event("sample.jpg"), None)

    files = saved_files(handler)
    assert files["json/classification_sample.json"] == {"error": '{"error": "Not found"}', "status": 404}
    assert files["json/ocr_sample.json"] == {"error": "TimeoutError: read timed out"}
    assert files["json/objectdetection_sample.json"] == ANSWERS["objectdetection"]


def test_api_types_setting_chooses_the_endpoints(handler):
    handler.API_TYPES = "ocr, unknown, objectdetection, ocr"

    handler.lambda_handler(s3_event("sample.jpg"), None)

    assert sorted(saved_files(handler)) == ["json/objectdetection_sample.json", "json/ocr_sample.json"]


def test_result_bucket_setting_saves_elsewhere(handler):
    handler.RESULT_BUCKET = "my-results"

    handler.lambda_handler(s3_event("sample.jpg"), None)

    assert handler.s3_client.download_fileobj.call_args.kwargs["Bucket"] == "my-images"
    assert {call.kwargs["Bucket"] for call in handler.s3_client.put_object.call_args_list} == {"my-results"}


@pytest.mark.parametrize(
    ("api_type", "key", "expected"),
    [
        ("classification", "sample.jpg", "json/classification_sample.json"),
        ("ocr", "photos/cat.v2.png", "json/photos/ocr_cat.v2.json"),
        ("celebrity", "photo", "json/celebrity_photo.json"),
    ],
)
def test_result_key(handler, api_type, key, expected):
    assert handler.result_key(api_type, key) == expected
