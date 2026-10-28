import importlib.util
import json
from unittest.mock import MagicMock

import pytest

from s3_manager.organizer import HANDLER_FILE


@pytest.fixture
def handler(monkeypatch):
    """The Lambda function module, loaded from its file, with a fake S3 client."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("organize_by_extension", HANDLER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.s3_client = MagicMock()
    return module


def s3_event(*keys, bucket="my-files"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}} for key in keys]}


def test_handler_moves_file_into_extension_folder(handler):
    response = handler.lambda_handler(s3_event("demo.csv"), None)

    handler.s3_client.copy_object.assert_called_once_with(
        Bucket="my-files", Key="csv/demo.csv", CopySource={"Bucket": "my-files", "Key": "demo.csv"}
    )
    handler.s3_client.delete_object.assert_called_once_with(Bucket="my-files", Key="demo.csv")
    assert json.loads(response["body"]) == {"moved": [{"from": "demo.csv", "to": "csv/demo.csv"}]}


def test_handler_decodes_key_and_uses_lowercase_folder(handler):
    # S3 sends "my photo.JPG" as "my+photo.JPG".
    handler.lambda_handler(s3_event("my+photo.JPG"), None)

    assert handler.s3_client.copy_object.call_args.kwargs["Key"] == "jpg/my photo.JPG"
    handler.s3_client.delete_object.assert_called_once_with(Bucket="my-files", Key="my photo.JPG")


def test_handler_skips_keys_already_in_a_folder(handler):
    # The copy made by the function triggers it again with csv/demo.csv: nothing happens, no loop.
    response = handler.lambda_handler(s3_event("csv/demo.csv"), None)

    handler.s3_client.copy_object.assert_not_called()
    handler.s3_client.delete_object.assert_not_called()
    assert json.loads(response["body"]) == {"moved": []}


@pytest.mark.parametrize("key", ["README", ".env"])
def test_handler_skips_files_without_extension(handler, key):
    handler.lambda_handler(s3_event(key), None)

    handler.s3_client.copy_object.assert_not_called()


def test_handler_moves_files_without_extension_to_unknown(handler):
    handler.MOVE_UNKNOWN = True

    handler.lambda_handler(s3_event("README"), None)

    assert handler.s3_client.copy_object.call_args.kwargs["Key"] == "unknown/README"


def test_handler_moves_every_record(handler):
    response = handler.lambda_handler(s3_event("a.csv", "b.png", "notes/c.txt"), None)

    moved = json.loads(response["body"])["moved"]
    assert [item["to"] for item in moved] == ["csv/a.csv", "png/b.png"]


@pytest.mark.parametrize(
    ("key", "expected"),
    [("image.jpg", "jpg"), ("archive.tar.GZ", "gz"), ("README", None), ("csv/demo.csv", None)],
)
def test_extension_folder(handler, key, expected):
    assert handler.extension_folder(key) == expected
