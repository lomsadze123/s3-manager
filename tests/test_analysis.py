import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from s3_manager import analysis, detection
from tests.conftest import make_client_error

ARN = "arn:aws:lambda:us-east-1:123456789012:function:image-analysis"


def test_lambda_zip_has_analysis_handler_at_the_top():
    archive = zipfile.ZipFile(io.BytesIO(detection.build_lambda_zip(analysis.HANDLER_FILE)))

    assert archive.namelist() == ["image_analysis.py"]
    assert "def lambda_handler" in archive.read("image_analysis.py").decode()


def test_parse_api_types_keeps_order_and_removes_duplicates():
    assert analysis.parse_api_types(" OCR, celebrity,ocr ") == ["ocr", "celebrity"]
    assert analysis.parse_api_types(analysis.DEFAULT_API_TYPES) == [
        "classification",
        "objectdetection",
        "celebrity",
        "ocr",
    ]


@pytest.mark.parametrize("text", ["ocr,faces", "", " , "])
def test_parse_api_types_rejects_unknown_or_empty(text):
    with pytest.raises(ValueError):
        analysis.parse_api_types(text)


def test_analysis_environment():
    assert analysis.analysis_environment("k", ["ocr", "celebrity"]) == {"API_NINJAS_KEY": "k", "API_TYPES": "ocr,celebrity"}
    assert analysis.analysis_environment("k", ["ocr"], "results")["RESULT_BUCKET"] == "results"


def test_result_keys_follow_the_assignment_format():
    assert analysis.result_keys("sample.jpg", analysis.API_TYPES) == {
        "classification": "json/classification_sample.json",
        "objectdetection": "json/objectdetection_sample.json",
        "celebrity": "json/celebrity_sample.json",
        "ocr": "json/ocr_sample.json",
    }
    assert analysis.result_key("ocr", "holiday/cat.png") == "json/holiday/ocr_cat.json"


def test_celebrity_name_comes_from_the_file_name():
    assert analysis.celebrity_name("people/Michael_Jordan.jpg") == "Michael Jordan"


def test_deploy_gives_permission_before_trigger_with_its_own_ids(client):
    client.get_caller_identity.return_value = {"Account": "123456789012"}
    client.get_function.side_effect = make_client_error("ResourceNotFoundException")
    client.create_function.return_value = {"FunctionArn": ARN}
    client.get_bucket_notification_configuration.return_value = {}

    arn, created = analysis.deploy_image_analysis(client, client, client, "my-images", "key", ["ocr"], "results")

    assert (arn, created) == (ARN, True)
    kwargs = client.create_function.call_args.kwargs
    assert kwargs["Handler"] == "image_analysis.lambda_handler"
    assert kwargs["Timeout"] == 60
    assert kwargs["Role"] == "arn:aws:iam::123456789012:role/LabRole"
    assert kwargs["Environment"] == {
        "Variables": {"API_NINJAS_KEY": "key", "API_TYPES": "ocr", "RESULT_BUCKET": "results"}
    }
    names = [name for name, _, _ in client.mock_calls]
    assert names.index("add_permission") < names.index("put_bucket_notification_configuration")
    configuration = client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"]
    lambdas = configuration["LambdaFunctionConfigurations"]
    assert {item["Id"] for item in lambdas} == {f"image-analysis-{ext[1:]}" for ext in detection.TRIGGER_EXTENSIONS}
    assert all(item["Events"] == ["s3:ObjectCreated:*"] for item in lambdas)


@pytest.mark.parametrize(
    ("trigger_id", "message"),
    [("object-detection-jpg", "object detection function"), ("organize-by-extension", "organize function")],
)
def test_deploy_refuses_bucket_with_other_trigger(client, trigger_id, message):
    client.get_bucket_notification_configuration.return_value = {"LambdaFunctionConfigurations": [{"Id": trigger_id}]}

    with pytest.raises(ValueError, match=message):
        analysis.deploy_image_analysis(client, client, client, "my-images", "key")

    client.create_function.assert_not_called()
    client.update_function_code.assert_not_called()


def test_remove_only_removes_analysis_triggers(client):
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "image-analysis-jpg"}, {"Id": "image-analysis-png"}, {"Id": "other"}]
    }

    assert analysis.remove_image_analysis(client, client, "my-images") == (2, True)
    configuration = client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"]
    assert configuration["LambdaFunctionConfigurations"] == [{"Id": "other"}]
    client.delete_function.assert_called_once_with(FunctionName="image-analysis")


def test_read_function_settings(client):
    client.get_function_configuration.return_value = {
        "Environment": {"Variables": {"API_TYPES": "ocr,celebrity", "RESULT_BUCKET": "results"}}
    }

    assert analysis.read_function_settings(client) == (["ocr", "celebrity"], "results")


def test_read_function_settings_defaults(client):
    client.get_function_configuration.return_value = {}

    assert analysis.read_function_settings(client) == (list(analysis.API_TYPES), None)


def test_analyze_image_deletes_old_results_and_waits_for_each(client, tmp_path, monkeypatch):
    photo = tmp_path / "sample.jpg"
    photo.write_bytes(b"fake")
    monkeypatch.setattr(analysis.objects, "upload_file", lambda *args, **kwargs: ("sample.jpg", None))
    waited = []
    monkeypatch.setattr(
        analysis.detection, "wait_for_result", lambda client, bucket, key, timeout: waited.append((bucket, key)) or []
    )

    key, results = analysis.analyze_image(client, photo, "my-images", ["ocr", "celebrity"], result_bucket="results")

    assert key == "sample.jpg"
    assert results == {"ocr": [], "celebrity": []}
    assert waited == [("results", "json/ocr_sample.json"), ("results", "json/celebrity_sample.json")]
    deleted = [call.kwargs["Key"] for call in client.delete_object.call_args_list]
    assert deleted == ["json/ocr_sample.json", "json/celebrity_sample.json"]


def test_analyze_image_rejects_keys_that_do_not_trigger(client, tmp_path):
    photo = tmp_path / "sample.gif"
    photo.write_bytes(b"fake")

    with pytest.raises(ValueError):
        analysis.analyze_image(client, photo, "my-images")


def test_query_analysis_api_posts_image_or_searches_name(monkeypatch):
    calls = []

    def fake(method):
        def send(url, **kwargs):
            calls.append((method, url, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: [])

        return send

    monkeypatch.setattr(analysis.requests, "post", fake("POST"))
    monkeypatch.setattr(analysis.requests, "get", fake("GET"))

    analysis.query_analysis_api("objectdetection", b"img", "sample.jpg", "k")
    analysis.query_analysis_api("celebrity", b"img", "michael_jordan.jpg", "k")

    assert calls[0][:2] == ("POST", analysis.API_URLS["objectdetection"])
    assert calls[0][2]["files"] == {"image": ("sample.jpg", b"img")}
    assert calls[1][:2] == ("GET", analysis.API_URLS["celebrity"])
    assert calls[1][2]["params"] == {"name": "michael jordan"}


def test_query_analysis_api_returns_errors_instead_of_raising(monkeypatch):
    response = SimpleNamespace(status_code=404, text='{"error": "Not found"}')
    monkeypatch.setattr(analysis.requests, "post", lambda url, **kwargs: response)

    assert analysis.query_analysis_api("classification", b"img", "a.jpg", "k") == {
        "error": '{"error": "Not found"}',
        "status": 404,
    }


def test_save_results_uses_the_s3_names(tmp_path):
    paths = analysis.save_results({"ocr": [{"text": "HI"}]}, "sample.jpg", tmp_path)

    assert paths == [tmp_path / "json" / "ocr_sample.json"]
    assert json.loads(paths[0].read_text()) == [{"text": "HI"}]


@pytest.mark.parametrize(
    ("api_type", "result", "expected"),
    [
        ("classification", {"error": "Not found", "status": 404}, "error (404): Not found"),
        ("objectdetection", [{"label": "cat", "confidence": "0.97"}, {"label": "sofa"}], "cat 0.97, sofa"),
        ("celebrity", [{"name": "michael jordan"}], "michael jordan"),
        ("celebrity", [], "nothing found"),
        ("ocr", [{"text": "API"}, {"text": "Ninjas"}], "API Ninjas"),
    ],
)
def test_summarize_result(api_type, result, expected):
    assert analysis.summarize_result(api_type, result) == expected
