import pytest
from botocore.exceptions import ClientError

from s3_manager.buckets import (
    bucket_exists,
    create_bucket,
    ensure_bucket,
    get_bucket_region,
    get_bucket_versioning,
    list_buckets,
    read_bucket_encryption,
    set_bucket_encryption,
    set_bucket_versioning,
)
from tests.conftest import make_client_error


def test_list_buckets_returns_names(client):
    client.list_buckets.return_value = {"Buckets": [{"Name": "first"}, {"Name": "second"}]}

    assert list_buckets(client) == ["first", "second"]


def test_create_bucket_in_us_east_1_has_no_location_constraint(client):
    client.meta.region_name = "us-east-1"

    create_bucket(client, "my-bucket")

    client.create_bucket.assert_called_once_with(Bucket="my-bucket")


def test_create_bucket_in_other_region_with_acl(client):
    create_bucket(client, "my-bucket", enable_acl=True)

    client.create_bucket.assert_called_once_with(
        Bucket="my-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        ObjectOwnership="ObjectWriter",
    )


def test_bucket_exists_returns_false_when_not_found(client):
    client.head_bucket.side_effect = make_client_error("404")

    assert bucket_exists(client, "missing-bucket") is False


def test_bucket_exists_raises_on_access_denied(client):
    client.head_bucket.side_effect = make_client_error("403")

    with pytest.raises(ClientError):
        bucket_exists(client, "someone-elses-bucket")


def test_ensure_bucket_skips_existing_bucket(client):
    assert ensure_bucket(client, "my-bucket") is False

    client.create_bucket.assert_not_called()


def test_ensure_bucket_creates_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    assert ensure_bucket(client, "my-bucket") is True

    client.create_bucket.assert_called_once_with(
        Bucket="my-bucket",
        CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
    )


@pytest.mark.parametrize("status", ["Enabled", "Suspended"])
def test_get_bucket_versioning_returns_status(client, status):
    client.get_bucket_versioning.return_value = {"Status": status}

    assert get_bucket_versioning(client, "my-bucket") == status
    client.get_bucket_versioning.assert_called_once_with(Bucket="my-bucket")


def test_get_bucket_versioning_returns_none_if_never_enabled(client):
    client.get_bucket_versioning.return_value = {}

    assert get_bucket_versioning(client, "my-bucket") is None


@pytest.mark.parametrize("enabled, status", [(True, "Enabled"), (False, "Suspended")])
def test_set_bucket_versioning(client, enabled, status):
    set_bucket_versioning(client, "my-bucket", enabled=enabled)

    client.put_bucket_versioning.assert_called_once_with(
        Bucket="my-bucket", VersioningConfiguration={"Status": status}
    )


def test_set_bucket_encryption_uses_aes256(client):
    set_bucket_encryption(client, "my-bucket")

    client.put_bucket_encryption.assert_called_once_with(
        Bucket="my-bucket",
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )


def test_read_bucket_encryption_returns_algorithm(client):
    client.get_bucket_encryption.return_value = {
        "ServerSideEncryptionConfiguration": {
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms"}}]
        }
    }

    assert read_bucket_encryption(client, "my-bucket") == "aws:kms"


def test_read_bucket_encryption_returns_none_without_configuration(client):
    client.get_bucket_encryption.side_effect = make_client_error("ServerSideEncryptionConfigurationNotFoundError")

    assert read_bucket_encryption(client, "my-bucket") is None


@pytest.mark.parametrize(
    ("location", "region"),
    [(None, "us-east-1"), ("EU", "eu-west-1"), ("eu-central-1", "eu-central-1")],
)
def test_get_bucket_region_translates_old_location_names(client, location, region):
    client.get_bucket_location.return_value = {"LocationConstraint": location}

    assert get_bucket_region(client, "my-bucket") == region
    client.get_bucket_location.assert_called_once_with(Bucket="my-bucket")
