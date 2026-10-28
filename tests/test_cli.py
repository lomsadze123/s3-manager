import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import call

import pytest
from typer.testing import CliRunner

from s3_manager import cli
from tests.conftest import make_client_error

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_client(client, monkeypatch):
    """Make every CLI command use the fake client instead of a real boto3 client."""
    monkeypatch.setattr(cli, "init_client", lambda region=None, service="s3": client)
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


def test_object_upload_by_type_puts_file_into_type_folder(client, tmp_path):
    # "fake image" is plain text for python-magic, so the folder comes from the .jpg extension.
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"fake image")

    result = runner.invoke(cli.app, ["object", "upload", str(photo), "my-bucket", "--by-type"])

    assert result.exit_code == 0
    assert "s3://my-bucket/jpg/photo.jpg" in result.output
    assert client.put_object.call_args.kwargs["Key"] == "jpg/photo.jpg"


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


def fake_aged_versions(client, *versions):
    """Versions as (key, version_id, age_in_days, is_latest), dated relative to now."""
    now = datetime.now(timezone.utc)
    client.get_paginator.return_value.paginate.return_value = [{"Versions": [
        {
            "Key": key,
            "VersionId": version_id,
            "LastModified": now - timedelta(days=age),
            "Size": 10,
            "IsLatest": is_latest,
        }
        for key, version_id, age, is_latest in versions
    ]}]


def test_object_old_versions_deletes_only_old_non_latest_versions(client):
    fake_aged_versions(
        client,
        ("a.txt", "a3", 1, True),
        ("a.txt", "a2", 30, False),      # recent, kept
        ("a.txt", "a1", 400, False),     # old, deleted
        ("b.txt", "b2", 300, True),      # old but latest, kept
        ("b.txt", "b1", 500, False),     # old, deleted
    )

    result = runner.invoke(cli.app, ["object", "old-versions", "my-bucket", "a.txt", "b.txt", "-y"])

    assert result.exit_code == 0
    assert "s3://my-bucket/a.txt has 3 version(s):" in result.output
    assert "s3://my-bucket/b.txt has 2 version(s):" in result.output
    assert "id a1  -> older than 6 month(s)" in result.output
    assert client.delete_object.call_args_list == [
        call(Bucket="my-bucket", Key="a.txt", VersionId="a1"),
        call(Bucket="my-bucket", Key="b.txt", VersionId="b1"),
    ]
    assert "2 old version(s) deleted." in result.output


def test_object_old_versions_dry_run_deletes_nothing(client):
    fake_aged_versions(client, ("a.txt", "a2", 1, True), ("a.txt", "a1", 400, False))

    result = runner.invoke(cli.app, ["object", "old-versions", "my-bucket", "a.txt", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run: 1 version(s)" in result.output
    client.delete_object.assert_not_called()


def test_object_old_versions_needs_confirmation(client):
    fake_aged_versions(client, ("a.txt", "a2", 1, True), ("a.txt", "a1", 400, False))

    result = runner.invoke(cli.app, ["object", "old-versions", "my-bucket", "a.txt"], input="n\n")

    assert result.exit_code == 1
    client.delete_object.assert_not_called()


def test_object_old_versions_months_zero_deletes_every_older_version(client):
    fake_aged_versions(client, ("a.txt", "a2", 1, True), ("a.txt", "a1", 2, False))

    result = runner.invoke(cli.app, ["object", "old-versions", "my-bucket", "a.txt", "--months", "0", "-y"])

    assert result.exit_code == 0
    client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="a.txt", VersionId="a1")


def test_object_old_versions_reports_missing_key_and_continues(client):
    fake_aged_versions(client, ("a.txt", "a2", 1, True), ("a.txt", "a1", 400, False))

    result = runner.invoke(cli.app, ["object", "old-versions", "my-bucket", "missing.txt", "a.txt", "-y"])

    assert result.exit_code == 1
    assert "Object 's3://my-bucket/missing.txt' does not exist." in result.output
    client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="a.txt", VersionId="a1")


def test_object_old_versions_nothing_to_delete(client):
    fake_aged_versions(client, ("a.txt", "a2", 1, True), ("a.txt", "a1", 30, False))

    result = runner.invoke(cli.app, ["object", "old-versions", "my-bucket", "a.txt"])

    assert result.exit_code == 0
    assert "Nothing to delete." in result.output
    client.delete_object.assert_not_called()


def test_object_old_versions_reports_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["object", "old-versions", "missing-bucket", "a.txt"])

    assert result.exit_code == 1
    assert "Bucket 'missing-bucket' does not exist." in result.output


def test_host_deploy_prints_website_url(client, tmp_path):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    page = tmp_path / "index.html"
    page.write_text("<h1>Beka Lomsadze</h1>")

    result = runner.invoke(cli.app, ["host", "deploy", "my-site", str(page)])

    assert result.exit_code == 0
    assert "Uploaded 1 file(s) to 'my-site'." in result.output
    assert "Website: http://my-site.s3-website-us-east-1.amazonaws.com" in result.output
    client.put_bucket_website.assert_called_once()


def test_host_deploy_spa_sets_index_as_error_document(client, tmp_path):
    client.get_bucket_location.return_value = {"LocationConstraint": "eu-central-1"}
    (tmp_path / "index.html").write_text("<div id='root'></div>")

    result = runner.invoke(cli.app, ["host", "deploy", "my-site", str(tmp_path), "--spa"])

    assert result.exit_code == 0
    assert "s3-website.eu-central-1" in result.output
    configuration = client.put_bucket_website.call_args.kwargs["WebsiteConfiguration"]
    assert configuration["ErrorDocument"] == {"Key": "index.html"}


def test_host_deploy_unbuilt_react_project_fails_before_aws_calls(client, tmp_path):
    (tmp_path / "package.json").write_text("{}")

    result = runner.invoke(cli.app, ["host", "deploy", "my-site", str(tmp_path)])

    # main() turns the ValueError into a short error message; the runner calls the app directly.
    assert isinstance(result.exception, ValueError)
    assert "npm run build" in str(result.exception)
    client.put_bucket_website.assert_not_called()


def test_host_config_needs_exactly_one_flag(client):
    for flags in ([], ["--enable", "--disable"]):
        result = runner.invoke(cli.app, ["host", "config", "my-site", *flags])

        assert result.exit_code == 1
        assert "Use either --enable or --disable." in result.output
    client.put_bucket_website.assert_not_called()


def test_host_config_disable_deletes_website_configuration(client):
    result = runner.invoke(cli.app, ["host", "config", "my-site", "--disable"])

    assert result.exit_code == 0
    assert "turned off" in result.output
    client.delete_bucket_website.assert_called_once_with(Bucket="my-site")


def test_host_status_reports_hosting_off(client):
    client.get_bucket_website.side_effect = make_client_error("NoSuchWebsiteConfiguration")

    result = runner.invoke(cli.app, ["host", "status", "my-site"])

    assert result.exit_code == 0
    assert "not enabled" in result.output


def test_host_status_shows_pages_and_url(client):
    client.get_bucket_website.return_value = {"IndexDocument": {"Suffix": "index.html"}}
    client.get_bucket_location.return_value = {"LocationConstraint": None}

    result = runner.invoke(cli.app, ["host", "status", "my-site"])

    assert result.exit_code == 0
    assert "index page: index.html" in result.output
    assert "(S3 default 404 page)" in result.output
    assert "http://my-site.s3-website-us-east-1.amazonaws.com" in result.output


def test_host_deploy_error_document_and_open(client, tmp_path, monkeypatch):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    (tmp_path / "index.html").write_text("<h1>Beka Lomsadze</h1>")
    (tmp_path / "error.html").write_text("<h1>404</h1>")
    opened = []
    monkeypatch.setattr(cli.typer, "launch", opened.append)

    result = runner.invoke(
        cli.app,
        ["host", "deploy", "my-site", str(tmp_path / "index.html"), "--error-document", "error.html", "--open"],
    )

    assert result.exit_code == 0
    assert "Uploaded 2 file(s)" in result.output
    assert opened == ["http://my-site.s3-website-us-east-1.amazonaws.com"]


def test_host_config_rejects_spa_with_error_document(client):
    result = runner.invoke(cli.app, ["host", "config", "my-site", "--enable", "--spa", "--error-document", "e.html"])

    assert result.exit_code == 1
    assert "either --spa or --error-document" in result.output
    client.put_bucket_website.assert_not_called()


def test_host_config_warns_when_error_page_is_missing(client):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    client.head_object.side_effect = make_client_error("404")

    result = runner.invoke(cli.app, ["host", "config", "my-site", "--enable", "--error-document", "error.html"])

    assert result.exit_code == 0
    assert "Warning: 'error.html' is not in the bucket yet" in result.output
    configuration = client.put_bucket_website.call_args.kwargs["WebsiteConfiguration"]
    assert configuration["ErrorDocument"] == {"Key": "error.html"}


def test_host_delete_keeps_files_without_purge(client):
    result = runner.invoke(cli.app, ["host", "delete", "my-site", "-y"])

    assert result.exit_code == 0
    assert "turned off" in result.output
    assert "Use --purge" in result.output
    client.delete_bucket_website.assert_called_once_with(Bucket="my-site")
    client.delete_object.assert_not_called()


def test_host_delete_purge_reports_deleted_objects(client):
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "index.html", "Size": 1, "LastModified": None}]}
    ]

    result = runner.invoke(cli.app, ["host", "delete", "my-site", "--purge", "-y"])

    assert result.exit_code == 0
    assert "Deleted 1 object(s)." in result.output


def test_host_delete_does_nothing_when_not_confirmed(client):
    result = runner.invoke(cli.app, ["host", "delete", "my-site", "--purge"], input="n\n")

    assert result.exit_code == 1
    client.delete_bucket_website.assert_not_called()


@pytest.fixture
def demo_site(tmp_path):
    """A small project folder like do-community/html_demo_site: two pages and an image."""
    (tmp_path / "index.html").write_text("<h1>Sammy the Shark</h1>")
    (tmp_path / "about.html").write_text("<p>About this site</p>")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "background.jpg").write_bytes(b"\xff\xd8\xff")
    return tmp_path


def test_host_bucket_with_source_runs_deploy(client, demo_site):
    client.get_bucket_location.return_value = {"LocationConstraint": None}

    result = runner.invoke(cli.app, ["host", "my-s3-static-host", "--source", str(demo_site)])

    assert result.exit_code == 0
    assert "Uploaded 3 file(s) to 'my-s3-static-host'." in result.output
    assert "Website: http://my-s3-static-host.s3-website-us-east-1.amazonaws.com" in result.output
    client.put_public_access_block.assert_called_once()
    client.put_bucket_policy.assert_called_once()
    client.put_bucket_website.assert_called_once_with(
        Bucket="my-s3-static-host", WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}}
    )


def test_host_deploy_accepts_source_option(client, demo_site):
    client.get_bucket_location.return_value = {"LocationConstraint": None}

    result = runner.invoke(cli.app, ["host", "deploy", "my-site", "-s", str(demo_site)])

    assert result.exit_code == 0
    assert "Uploaded 3 file(s) to 'my-site'." in result.output


@pytest.mark.parametrize("extra", [[], ["--source"]], ids=["no-path", "path-and-source"])
def test_host_deploy_needs_path_or_source_once(client, demo_site, extra):
    args = ["host", "my-site", str(demo_site), *extra, str(demo_site)] if extra else ["host", "my-site"]

    result = runner.invoke(cli.app, args)

    assert result.exit_code == 1
    assert "as PATH or with --source" in result.output
    client.put_bucket_website.assert_not_called()


def test_host_subcommands_are_not_taken_as_bucket_names(client):
    client.get_bucket_website.return_value = {"IndexDocument": {"Suffix": "index.html"}}
    client.get_bucket_location.return_value = {"LocationConstraint": None}

    result = runner.invoke(cli.app, ["host", "status", "my-site"])

    assert result.exit_code == 0
    assert "Static website hosting is enabled on 'my-site'." in result.output
    client.put_bucket_website.assert_not_called()


def test_main_py_runs_the_cli():
    project = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [sys.executable, "main.py", "host", "deploy", "--help"], cwd=project, capture_output=True, text=True
    )

    assert result.returncode == 0
    assert "--source" in result.stdout


@pytest.fixture
def settings(monkeypatch):
    """Fake .env settings for the detect commands."""
    values = {"API_NINJAS_KEY": "test-key"}
    monkeypatch.setattr(cli, "read_setting", values.get)
    return values


def test_detect_deploy_needs_api_key(client, settings):
    settings.clear()

    result = runner.invoke(cli.app, ["detect", "deploy", "my-photos"])

    assert result.exit_code == 1
    assert "Set API_NINJAS_KEY in .env first" in result.output
    client.create_function.assert_not_called()


def test_detect_deploy_creates_function_and_trigger(client, settings):
    client.get_bucket_location.return_value = {"LocationConstraint": "eu-central-1"}
    client.get_caller_identity.return_value = {"Account": "123456789012"}
    client.get_function.side_effect = make_client_error("ResourceNotFoundException")
    client.create_function.return_value = {"FunctionArn": "arn:aws:lambda:eu-central-1:1:function:object-detection"}
    client.get_bucket_notification_configuration.return_value = {}

    result = runner.invoke(cli.app, ["detect", "deploy", "my-photos"])

    assert result.exit_code == 0
    assert "Lambda function 'object-detection' created in eu-central-1 (api-ninjas)." in result.output
    assert client.create_function.call_args.kwargs["Environment"] == {
        "Variables": {"PROVIDER": "api-ninjas", "API_NINJAS_KEY": "test-key"}
    }
    client.put_bucket_notification_configuration.assert_called_once()


def test_detect_deploy_uses_role_from_settings(client, settings):
    settings["LAMBDA_ROLE_ARN"] = "arn:aws:iam::1:role/MyRole"
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    client.update_function_configuration.return_value = {"FunctionArn": "arn"}
    client.get_bucket_notification_configuration.return_value = {}

    result = runner.invoke(cli.app, ["detect", "deploy", "my-photos"])

    assert result.exit_code == 0
    assert "updated" in result.output
    assert client.update_function_configuration.call_args.kwargs["Role"] == "arn:aws:iam::1:role/MyRole"


def test_detect_image_prints_objects(client, tmp_path, monkeypatch):
    photo = tmp_path / "cat.jpg"
    photo.write_bytes(b"fake")
    result_json = [
        {"label": "cat", "confidence": "0.97", "bounding_box": {"x1": 1, "y1": 2, "x2": 3, "y2": 4}},
        {"label": "sofa", "confidence": "0.31", "bounding_box": {"x1": 0, "y1": 0, "x2": 9, "y2": 9}},
    ]
    monkeypatch.setattr(cli.detection, "detect_image", lambda *args, **kwargs: ("cat.jpg", result_json))

    result = runner.invoke(cli.app, ["detect", "image", "my-photos", str(photo)])

    assert result.exit_code == 0
    assert "result in s3://my-photos/cat.json" in result.output
    assert "cat             0.97  box (1, 2) - (3, 4)" in result.output
    assert "sofa" not in result.output
    assert "1 object(s) found." in result.output


def test_detect_local_calls_api_without_aws(client, settings, tmp_path, monkeypatch):
    photo = tmp_path / "cat.jpg"
    photo.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(cli.detection, "query_detection_api", lambda *args: calls.append(args) or [])

    result = runner.invoke(cli.app, ["detect", "local", str(photo)])

    assert result.exit_code == 0
    assert calls == [(b"fake", "api-ninjas", "test-key")]
    assert "No objects found" in result.output
    assert client.mock_calls == []


def test_detect_delete_removes_trigger_and_function(client):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    client.get_bucket_notification_configuration.return_value = {
        "LambdaFunctionConfigurations": [{"Id": "object-detection-jpg"}]
    }

    result = runner.invoke(cli.app, ["detect", "delete", "my-photos", "-y"])

    assert result.exit_code == 0
    assert "Removed 1 trigger(s)" in result.output
    assert "Lambda function 'object-detection' deleted." in result.output
    client.delete_function.assert_called_once_with(FunctionName="object-detection")
