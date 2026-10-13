import pytest
from typer.testing import CliRunner

from s3_manager import cli
from tests.conftest import make_client_error

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_client(client, monkeypatch):
    """Make every CLI command use the fake client instead of a real boto3 client."""
    monkeypatch.setattr(cli, "init_client", lambda: client)
    return client


def test_bucket_delete_reports_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["bucket", "delete", "missing-bucket", "--yes"])

    assert result.exit_code == 0
    assert "does not exist" in result.output
    client.delete_bucket.assert_not_called()


def test_bucket_delete_deletes_existing_bucket(client):
    result = runner.invoke(cli.app, ["bucket", "delete", "my-bucket", "--yes"])

    assert result.exit_code == 0
    assert "deleted" in result.output
    client.delete_bucket.assert_called_once_with(Bucket="my-bucket")


def test_bucket_delete_keeps_bucket_when_not_confirmed(client):
    result = runner.invoke(cli.app, ["bucket", "delete", "my-bucket"], input="n\n")

    assert result.exit_code == 1
    client.delete_bucket.assert_not_called()


def test_object_upload_reports_method(client, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"fake image")

    result = runner.invoke(cli.app, ["object", "upload", str(photo), "my-bucket", "--method", "small"])

    assert result.exit_code == 0
    assert "s3://my-bucket/photo.jpg (small upload)" in result.output
    client.put_object.assert_called_once()


def test_lifecycle_set_uses_120_days_by_default(client):
    result = runner.invoke(cli.app, ["lifecycle", "set", "my-bucket"])

    assert result.exit_code == 0
    assert "deleted 120 days after creation" in result.output
    rule = client.put_bucket_lifecycle_configuration.call_args.kwargs["LifecycleConfiguration"]["Rules"][0]
    assert rule["Expiration"] == {"Days": 120}


def test_lifecycle_get_reports_missing_policy(client):
    client.get_bucket_lifecycle_configuration.side_effect = make_client_error("NoSuchLifecycleConfiguration")

    result = runner.invoke(cli.app, ["lifecycle", "get", "my-bucket"])

    assert result.exit_code == 0
    assert "has no lifecycle policy" in result.output
