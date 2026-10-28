import io
import zipfile

import pytest

from s3_manager import organizer
from tests.conftest import make_client_error

ARN = "arn:aws:lambda:eu-central-1:123456789012:function:organize-by-extension"


def test_organizer_environment():
    assert organizer.organizer_environment() == {"MOVE_UNKNOWN": "false"}
    assert organizer.organizer_environment(include_unknown=True) == {"MOVE_UNKNOWN": "true"}


def test_set_organize_trigger_keeps_other_notifications_and_replaces_own(client):
    other = {"Id": "thumbnails", "LambdaFunctionArn": "arn:other", "Events": ["s3:ObjectRemoved:*"]}
    client.get_bucket_notification_configuration.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "QueueConfigurations": [{"Id": "queue"}],
        "LambdaFunctionConfigurations": [other, {"Id": "organize-by-extension", "LambdaFunctionArn": "arn:old"}],
    }

    organizer.set_organize_trigger(client, "my-files", ARN)

    configuration = client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"]
    assert "ResponseMetadata" not in configuration
    assert configuration["QueueConfigurations"] == [{"Id": "queue"}]
    assert configuration["LambdaFunctionConfigurations"] == [
        other,
        {"Id": "organize-by-extension", "LambdaFunctionArn": ARN, "Events": ["s3:ObjectCreated:*"]},
    ]


def test_set_organize_trigger_refuses_bucket_with_detection(client):
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "object-detection-jpg"}]
    }

    with pytest.raises(ValueError, match="already runs the object detection function"):
        organizer.set_organize_trigger(client, "my-photos", ARN)
    client.put_bucket_notification_configuration.assert_not_called()


def test_remove_organize_trigger_counts_removed_config(client):
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "organize-by-extension"}, {"Id": "x"}]
    }

    assert organizer.remove_organize_trigger(client, "my-files") == 1
    configuration = client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"]
    assert configuration["LambdaFunctionConfigurations"] == [{"Id": "x"}]


def test_remove_organize_trigger_without_trigger_changes_nothing(client):
    client.get_bucket_notification_configuration.return_value = {}

    assert organizer.remove_organize_trigger(client, "my-files") == 0
    client.put_bucket_notification_configuration.assert_not_called()


def test_deploy_organizer_creates_function_and_gives_permission_before_trigger(client):
    client.get_caller_identity.return_value = {"Account": "123456789012"}
    client.get_function.side_effect = make_client_error("ResourceNotFoundException")
    client.create_function.return_value = {"FunctionArn": ARN}
    client.get_bucket_notification_configuration.return_value = {}

    arn, created = organizer.deploy_organizer(client, client, client, "my-files", include_unknown=True)

    assert (arn, created) == (ARN, True)
    kwargs = client.create_function.call_args.kwargs
    assert kwargs["FunctionName"] == "organize-by-extension"
    assert kwargs["Handler"] == "organize_by_extension.lambda_handler"
    assert kwargs["Role"] == "arn:aws:iam::123456789012:role/LabRole"
    assert kwargs["Environment"] == {"Variables": {"MOVE_UNKNOWN": "true"}}
    with zipfile.ZipFile(io.BytesIO(kwargs["Code"]["ZipFile"])) as archive:
        assert archive.namelist() == ["organize_by_extension.py"]
    names = [name for name, _, _ in client.mock_calls]
    assert names.index("add_permission") < names.index("put_bucket_notification_configuration")


def test_deploy_organizer_checks_bucket_before_creating_function(client):
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "object-detection-png"}]
    }

    with pytest.raises(ValueError, match="detect delete"):
        organizer.deploy_organizer(client, client, client, "my-photos")
    client.create_function.assert_not_called()
    client.update_function_code.assert_not_called()


def test_remove_organizer_deletes_function(client):
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "organize-by-extension"}]
    }

    assert organizer.remove_organizer(client, client, "my-files") == (1, True)
    client.delete_function.assert_called_once_with(FunctionName="organize-by-extension")


def test_remove_organizer_reports_missing_function(client):
    client.get_bucket_notification_configuration.return_value = {}
    client.get_function.side_effect = make_client_error("ResourceNotFoundException")

    assert organizer.remove_organizer(client, client, "my-files") == (0, False)
    client.delete_function.assert_not_called()
