import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from s3_manager import detection
from tests.conftest import make_client_error
from tests.test_objects import JPEG_BYTES

ARN = "arn:aws:lambda:us-east-1:123456789012:function:object-detection"


def test_build_lambda_zip_has_handler_at_the_top():
    archive = zipfile.ZipFile(io.BytesIO(detection.build_lambda_zip()))

    assert archive.namelist() == ["object_detection.py"]
    assert "def lambda_handler" in archive.read("object_detection.py").decode()


def test_default_role_arn_is_lab_role():
    assert detection.default_role_arn("123456789012") == "arn:aws:iam::123456789012:role/LabRole"


def test_detection_environment_has_provider_and_its_key():
    assert detection.detection_environment("api-ninjas", "k") == {"PROVIDER": "api-ninjas", "API_NINJAS_KEY": "k"}
    assert detection.detection_environment("huggingface", "t") == {"PROVIDER": "huggingface", "HF_API_TOKEN": "t"}
    with pytest.raises(ValueError, match="Provider must be one of"):
        detection.detection_environment("openai", "k")


def test_deploy_detection_lambda_creates_missing_function(client):
    client.get_function.side_effect = make_client_error("ResourceNotFoundException")
    client.create_function.return_value = {"FunctionArn": ARN}

    arn, created = detection.deploy_detection_lambda(client, b"zip", "role-arn", environment={"PROVIDER": "x"})

    assert (arn, created) == (ARN, True)
    kwargs = client.create_function.call_args.kwargs
    assert kwargs["Code"] == {"ZipFile": b"zip"}
    assert kwargs["Role"] == "role-arn"
    assert kwargs["Handler"] == "object_detection.lambda_handler"
    assert kwargs["Runtime"] == detection.LAMBDA_RUNTIME
    assert kwargs["Environment"] == {"Variables": {"PROVIDER": "x"}}
    client.get_waiter.assert_called_once_with("function_active_v2")
    client.update_function_code.assert_not_called()


def test_deploy_detection_lambda_updates_existing_function(client):
    client.update_function_configuration.return_value = {"FunctionArn": ARN}

    arn, created = detection.deploy_detection_lambda(client, b"zip", "role-arn")

    assert (arn, created) == (ARN, False)
    client.update_function_code.assert_called_once_with(FunctionName="object-detection", ZipFile=b"zip")
    assert client.update_function_configuration.call_args.kwargs["Role"] == "role-arn"
    client.create_function.assert_not_called()


def test_deploy_detection_lambda_raises_other_errors(client):
    client.get_function.side_effect = make_client_error("AccessDeniedException")

    with pytest.raises(ClientError):
        detection.deploy_detection_lambda(client, b"zip", "role-arn")


def test_allow_s3_invoke_limits_permission_to_bucket_and_account(client):
    assert detection.allow_s3_invoke(client, "object-detection", "my.photos", "123456789012") is True

    client.add_permission.assert_called_once_with(
        FunctionName="object-detection",
        StatementId="s3-invoke-my-photos",
        Action="lambda:InvokeFunction",
        Principal="s3.amazonaws.com",
        SourceArn="arn:aws:s3:::my.photos",
        SourceAccount="123456789012",
    )


def test_allow_s3_invoke_ignores_existing_permission(client):
    client.add_permission.side_effect = make_client_error("ResourceConflictException")

    assert detection.allow_s3_invoke(client, "object-detection", "my-photos", "123456789012") is False


def test_set_detection_trigger_keeps_other_notifications(client):
    other = {"Id": "thumbnails", "LambdaFunctionArn": "arn:other", "Events": ["s3:ObjectCreated:*"]}
    client.get_bucket_notification_configuration.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "QueueConfigurations": [{"Id": "queue"}],
        "LambdaFunctionConfigurations": [other, {"Id": "object-detection-jpg", "LambdaFunctionArn": "arn:old"}],
    }

    detection.set_detection_trigger(client, "my-photos", ARN)

    configuration = client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"]
    assert "ResponseMetadata" not in configuration
    assert configuration["QueueConfigurations"] == [{"Id": "queue"}]
    lambdas = configuration["LambdaFunctionConfigurations"]
    assert lambdas[0] == other
    suffixes = [item["Filter"]["Key"]["FilterRules"][0]["Value"] for item in lambdas[1:]]
    assert suffixes == [".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"]
    assert all(item["LambdaFunctionArn"] == ARN for item in lambdas[1:])
    assert ".json" not in suffixes


def test_remove_detection_trigger_counts_removed_configs(client):
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "object-detection-jpg"}, {"Id": "object-detection-png"}, {"Id": "x"}]
    }

    assert detection.remove_detection_trigger(client, "my-photos") == 2
    configuration = client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"]
    assert configuration["LambdaFunctionConfigurations"] == [{"Id": "x"}]


def test_remove_detection_trigger_without_triggers_changes_nothing(client):
    client.get_bucket_notification_configuration.return_value = {}

    assert detection.remove_detection_trigger(client, "my-photos") == 0
    client.put_bucket_notification_configuration.assert_not_called()


def test_deploy_object_detection_gives_permission_before_trigger(client):
    client.get_caller_identity.return_value = {"Account": "123456789012"}
    client.update_function_configuration.return_value = {"FunctionArn": ARN}
    client.get_bucket_notification_configuration.return_value = {}

    arn, _ = detection.deploy_object_detection(client, client, client, "my-photos", "api-ninjas", "key")

    assert arn == ARN
    configuration = client.update_function_configuration.call_args.kwargs
    assert configuration["Role"] == "arn:aws:iam::123456789012:role/LabRole"
    assert configuration["Environment"] == {"Variables": {"PROVIDER": "api-ninjas", "API_NINJAS_KEY": "key"}}
    names = [name for name, _, _ in client.mock_calls]
    assert names.index("add_permission") < names.index("put_bucket_notification_configuration")


def test_remove_object_detection_reports_missing_function(client):
    client.get_bucket_notification_configuration.return_value = {}
    client.get_function.side_effect = make_client_error("ResourceNotFoundException")

    assert detection.remove_object_detection(client, client, "my-photos") == (0, False)
    client.delete_function.assert_not_called()


def test_wait_for_result_polls_until_json_exists(client):
    client.head_object.side_effect = [make_client_error("404"), make_client_error("404"), {"ContentLength": 2}]
    client.get_object.return_value = {"Body": io.BytesIO(b'[{"label": "cat"}]')}
    sleeps = []

    result = detection.wait_for_result(client, "my-photos", "cat.json", sleep=sleeps.append, clock=lambda: 0)

    assert result == [{"label": "cat"}]
    assert sleeps == [2, 2]


def test_wait_for_result_times_out(client):
    client.head_object.side_effect = make_client_error("404")
    times = iter([0, 30, 61])

    with pytest.raises(TimeoutError, match="CloudWatch"):
        detection.wait_for_result(client, "my-photos", "cat.json", sleep=lambda _: None, clock=lambda: next(times))


def test_detect_image_deletes_old_result_and_uploads(client, tmp_path, monkeypatch):
    photo = tmp_path / "cat.jpg"
    photo.write_bytes(JPEG_BYTES)
    monkeypatch.setattr(detection, "wait_for_result", lambda *args, **kwargs: [{"label": "cat"}])

    key, result = detection.detect_image(client, photo, "my-photos")

    assert (key, result) == ("cat.jpg", [{"label": "cat"}])
    client.delete_object.assert_called_once_with(Bucket="my-photos", Key="cat.json")
    assert client.put_object.call_args.kwargs["ContentType"] == "image/jpeg"


def test_detect_image_rejects_keys_that_do_not_trigger(client, tmp_path):
    photo = tmp_path / "cat.gif"
    photo.write_bytes(b"GIF89a")

    with pytest.raises(ValueError, match="trigger the detection"):
        detection.detect_image(client, photo, "my-photos")
    assert client.mock_calls == []


def test_normalize_detections_api_ninjas_format():
    result = [
        {"label": "dog", "confidence": "0.42", "bounding_box": {"x1": "1", "y1": "2", "x2": "3", "y2": "4"}},
        {"label": "cat", "confidence": "0.97", "bounding_box": {"x1": 10, "y1": 20, "x2": 30, "y2": 40}},
    ]

    assert detection.normalize_detections(result) == [
        {"label": "cat", "confidence": 0.97, "box": (10, 20, 30, 40)},
        {"label": "dog", "confidence": 0.42, "box": (1, 2, 3, 4)},
    ]
    assert [item["label"] for item in detection.normalize_detections(result, min_confidence=0.5)] == ["cat"]


def test_normalize_detections_huggingface_format():
    result = [{"label": "cat", "score": 0.99, "box": {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}}]

    assert detection.normalize_detections(result) == [{"label": "cat", "confidence": 0.99, "box": (1, 2, 3, 4)}]


def test_normalize_detections_raises_saved_error():
    with pytest.raises(ValueError, match="Detection failed: Invalid API Key"):
        detection.normalize_detections({"error": "Invalid API Key", "status": 401})


def test_query_detection_api_sends_image_to_api_ninjas(monkeypatch):
    post = MagicMock(return_value=SimpleNamespace(status_code=200, json=lambda: [], text="[]"))
    monkeypatch.setattr(detection.requests, "post", post)

    assert detection.query_detection_api(b"img", "api-ninjas", "key") == []
    assert post.call_args.args == (detection.API_NINJAS_URL,)
    assert post.call_args.kwargs["headers"] == {"X-Api-Key": "key"}
    assert post.call_args.kwargs["files"] == {"image": b"img"}


def test_query_detection_api_raises_on_error_status(monkeypatch):
    response = SimpleNamespace(status_code=400, text=json.dumps({"error": "bad image"}))
    monkeypatch.setattr(detection.requests, "post", MagicMock(return_value=response))

    with pytest.raises(ValueError, match="api-ninjas answered 400"):
        detection.query_detection_api(b"img", "api-ninjas", "key")
