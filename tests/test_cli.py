from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from s3_manager import cli
from tests.conftest import make_client_error

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_client(client, monkeypatch):
    """Make every CLI command use the fake client instead of a real boto3 client."""
    monkeypatch.setattr(cli, "init_client", lambda region=None: client)
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


def test_object_file_del_deletes_existing_object(client):
    client.head_object.return_value = {"ContentLength": 10, "ContentType": "image/jpeg"}

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "dev/photo.jpg", "-del"])

    assert result.exit_code == 0
    assert "Object 's3://my-bucket/dev/photo.jpg' deleted." in result.output
    client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="dev/photo.jpg")


def test_object_file_accepts_long_delete_flag(client):
    client.head_object.return_value = {"ContentLength": 10, "ContentType": "image/jpeg"}

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg", "--delete"])

    assert result.exit_code == 0
    client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="photo.jpg")


def test_object_file_del_reports_missing_object(client):
    client.head_object.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "missing.jpg", "-del"])

    assert result.exit_code == 0
    assert "does not exist. Nothing to delete." in result.output
    client.delete_object.assert_not_called()


def test_object_file_without_flag_only_shows_info(client):
    client.head_object.return_value = {"ContentLength": 10, "ContentType": "image/jpeg"}

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg"])

    assert result.exit_code == 0
    assert "10 bytes, image/jpeg" in result.output
    client.delete_object.assert_not_called()


def test_object_file_reports_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["object", "file", "missing-bucket", "photo.jpg", "-del"])

    assert result.exit_code == 1
    assert "Bucket 'missing-bucket' does not exist." in result.output
    client.delete_object.assert_not_called()


@pytest.mark.parametrize(
    "response, message",
    [
        ({"Status": "Enabled"}, "Versioning is enabled on bucket 'my-bucket'."),
        ({"Status": "Suspended"}, "Versioning is suspended on bucket 'my-bucket'"),
        ({}, "Versioning has never been enabled on bucket 'my-bucket'."),
    ],
)
def test_bucket_versioning_status(client, response, message):
    client.get_bucket_versioning.return_value = response

    result = runner.invoke(cli.app, ["bucket", "versioning", "my-bucket", "-vs"])

    assert result.exit_code == 0
    assert message in result.output
    client.put_bucket_versioning.assert_not_called()


def test_bucket_versioning_enable(client):
    result = runner.invoke(cli.app, ["bucket", "versioning", "my-bucket", "--enable"])

    assert result.exit_code == 0
    assert "Versioning of bucket 'my-bucket' enabled." in result.output
    client.put_bucket_versioning.assert_called_once_with(
        Bucket="my-bucket", VersioningConfiguration={"Status": "Enabled"}
    )


def test_bucket_versioning_needs_a_flag(client):
    result = runner.invoke(cli.app, ["bucket", "versioning", "my-bucket"])

    assert result.exit_code == 1
    client.get_bucket_versioning.assert_not_called()


def test_bucket_versioning_reports_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["bucket", "versioning", "missing-bucket", "-vs"])

    assert result.exit_code == 1
    assert "Bucket 'missing-bucket' does not exist." in result.output


def fake_versions(client, *versions):
    client.get_paginator.return_value.paginate.return_value = [{"Versions": [
        {
            "Key": "photo.jpg",
            "VersionId": version_id,
            "LastModified": datetime(2026, 10, day, 12, 30, tzinfo=timezone.utc),
            "Size": 10,
            "IsLatest": day == max(d for _, d in versions),
        }
        for version_id, day in versions
    ]}]


def test_object_file_ver_lists_versions(client):
    fake_versions(client, ("v1", 1), ("v2", 2))

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg", "-ver"])

    assert result.exit_code == 0
    assert "s3://my-bucket/photo.jpg has 2 version(s):" in result.output
    assert "1. created 2026-10-02 12:30:00 UTC, 10 bytes, id v2  (latest)" in result.output
    assert "2. created 2026-10-01 12:30:00 UTC, 10 bytes, id v1" in result.output


def test_object_file_ver_reports_missing_object(client):
    client.get_paginator.return_value.paginate.return_value = [{}]

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "missing.jpg", "-ver"])

    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_object_file_rp_uploads_previous_version(client):
    fake_versions(client, ("v1", 1), ("v2", 2))
    client.copy_object.return_value = {"VersionId": "v3"}

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg", "-rp"])

    assert result.exit_code == 0
    assert "Version v1 (created 2026-10-01 12:30:00 UTC) uploaded again as new version v3." in result.output
    client.copy_object.assert_called_once_with(
        Bucket="my-bucket", Key="photo.jpg", CopySource={"Bucket": "my-bucket", "Key": "photo.jpg", "VersionId": "v1"}
    )


def test_object_file_rejects_two_flags(client):
    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg", "-ver", "-del"])

    assert result.exit_code == 1
    assert "only one of" in result.output
    client.delete_object.assert_not_called()


def fake_root_keys(client, *keys):
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": key} for key in keys]}
    ]


def test_bucket_organize_shows_count_per_extension(client):
    fake_root_keys(client, "image.jpg", "demo.csv", "users.csv")

    result = runner.invoke(cli.app, ["bucket", "organize", "my-bucket", "-ext"])

    assert result.exit_code == 0
    assert result.output.splitlines() == ["csv - 2", "jpg - 1"]
    assert client.copy_object.call_count == 3


def test_bucket_organize_needs_ext_flag(client):
    result = runner.invoke(cli.app, ["bucket", "organize", "my-bucket"])

    assert result.exit_code == 1
    assert "Pass -ext" in result.output
    client.copy_object.assert_not_called()


def test_bucket_organize_reports_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["bucket", "organize", "missing-bucket", "-ext"])

    assert result.exit_code == 1
    assert "does not exist" in result.output
    client.copy_object.assert_not_called()


def test_bucket_organize_reports_nothing_to_move(client):
    fake_root_keys(client, "README")

    result = runner.invoke(cli.app, ["bucket", "organize", "my-bucket", "--by-extension"])

    assert result.exit_code == 0
    assert "Nothing to organize" in result.output


def test_bucket_create_with_region_and_versioning(client, monkeypatch):
    regions = []

    def fake_init_client(region=None):
        regions.append(region)
        client.meta.region_name = region
        return client

    monkeypatch.setattr(cli, "init_client", fake_init_client)

    result = runner.invoke(cli.app, ["bucket", "create", "my-bucket", "--region", "us-west-2", "-vers"])

    assert result.exit_code == 0
    assert regions == ["us-west-2"]
    assert "Bucket 'my-bucket' created in us-west-2." in result.output
    client.create_bucket.assert_called_once_with(
        Bucket="my-bucket", CreateBucketConfiguration={"LocationConstraint": "us-west-2"}
    )
    client.put_bucket_versioning.assert_called_once_with(
        Bucket="my-bucket", VersioningConfiguration={"Status": "Enabled"}
    )


def test_bucket_create_without_versioning_flag(client):
    result = runner.invoke(cli.app, ["bucket", "create", "my-bucket"])

    assert result.exit_code == 0
    client.put_bucket_versioning.assert_not_called()


def test_bucket_encryption_enable_and_status(client):
    client.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        }
    }

    result = runner.invoke(cli.app, ["bucket", "encryption", "my-bucket", "--enable", "-es"])

    assert result.exit_code == 0
    assert "set to AES256" in result.output
    assert "encrypts new objects with AES256" in result.output
    client.put_bucket_encryption.assert_called_once()


def test_bucket_encryption_needs_a_flag(client):
    result = runner.invoke(cli.app, ["bucket", "encryption", "my-bucket"])

    assert result.exit_code == 1
    assert "Pass -es" in result.output
    client.put_bucket_encryption.assert_not_called()


def test_bucket_organize_unknown_moves_files_without_extension(client):
    fake_root_keys(client, "README")

    result = runner.invoke(cli.app, ["bucket", "organize", "my-bucket", "-ext", "--unknown"])

    assert result.exit_code == 0
    assert result.output.splitlines() == ["unknown - 1"]


def test_object_list_shows_objects_and_total(client):
    client.get_paginator.return_value.paginate.return_value = [{"Contents": [
        {"Key": "a.txt", "Size": 3, "LastModified": datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)},
        {"Key": "dev/b.jpg", "Size": 7, "LastModified": datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc)},
    ]}]

    result = runner.invoke(cli.app, ["object", "list", "my-bucket"])

    assert result.exit_code == 0
    assert "a.txt  3 bytes  2026-10-01 12:30:00 UTC" in result.output
    assert "2 object(s), 10 bytes in total." in result.output


def test_object_list_empty_bucket(client):
    client.get_paginator.return_value.paginate.return_value = [{}]

    result = runner.invoke(cli.app, ["object", "list", "my-bucket"])

    assert result.exit_code == 0
    assert "No objects in s3://my-bucket/" in result.output


def test_object_file_rbt_uploads_chosen_version(client):
    fake_versions(client, ("v1", 1), ("v2", 2), ("v3", 3))
    client.copy_object.return_value = {"VersionId": "v4"}

    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg", "-rbt", "v1"])

    assert result.exit_code == 0
    assert "Version v1 (created 2026-10-01 12:30:00 UTC) uploaded again as new version v4." in result.output


def test_object_file_rejects_rbt_with_rp(client):
    result = runner.invoke(cli.app, ["object", "file", "my-bucket", "photo.jpg", "-rp", "-rbt", "v1"])

    assert result.exit_code == 1
    assert "only one of" in result.output
    client.copy_object.assert_not_called()


def test_policy_create_multiple_needs_confirmation(client):
    result = runner.invoke(cli.app, ["policy", "create-multiple", "my-bucket"], input="n\n")

    assert result.exit_code == 1
    client.put_bucket_policy.assert_not_called()


def test_policy_create_multiple_applies_policy(client):
    result = runner.invoke(cli.app, ["policy", "create-multiple", "my-bucket", "--yes"])

    assert result.exit_code == 0
    client.put_public_access_block.assert_called_once()
    assert "s3:DeleteObject" in client.put_bucket_policy.call_args.kwargs["Policy"]
