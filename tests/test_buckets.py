import pytest
from botocore.exceptions import ClientError

from s3_manager.buckets import bucket_exists, create_bucket, list_buckets
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
