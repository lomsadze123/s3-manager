import json

from s3_manager.policies import (
    create_bucket_policy,
    ensure_bucket_policy,
    generate_public_read_policy,
    read_bucket_policy,
)
from tests.conftest import make_client_error


def test_generate_public_read_policy():
    policy = generate_public_read_policy("my-bucket")
    statement = policy["Statement"][0]

    assert policy["Version"] == "2012-10-17"
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == "*"
    assert statement["Action"] == "s3:GetObject"
    assert statement["Resource"] == "arn:aws:s3:::my-bucket/*"


def test_create_bucket_policy_sends_policy_as_json_string(client):
    create_bucket_policy(client, "my-bucket")

    sent = client.put_bucket_policy.call_args.kwargs
    assert sent["Bucket"] == "my-bucket"
    assert json.loads(sent["Policy"]) == generate_public_read_policy("my-bucket")


def test_read_bucket_policy_returns_none_when_bucket_has_no_policy(client):
    client.get_bucket_policy.side_effect = make_client_error("NoSuchBucketPolicy")

    assert read_bucket_policy(client, "my-bucket") is None


def test_generate_public_read_policy_limited_to_prefixes():
    policy = generate_public_read_policy("my-bucket", ["/dev", "test/"])

    assert policy["Statement"][0]["Resource"] == [
        "arn:aws:s3:::my-bucket/dev/*",
        "arn:aws:s3:::my-bucket/test/*",
    ]


def test_ensure_bucket_policy_skips_bucket_with_policy(client):
    client.get_bucket_policy.return_value = {"Policy": json.dumps({"Statement": []})}

    assert ensure_bucket_policy(client, "my-bucket", ["dev", "test"]) is False

    client.put_public_access_block.assert_not_called()
    client.put_bucket_policy.assert_not_called()


def test_ensure_bucket_policy_applies_prefix_policy_when_missing(client):
    client.get_bucket_policy.side_effect = make_client_error("NoSuchBucketPolicy")

    assert ensure_bucket_policy(client, "my-bucket", ["dev", "test"]) is True

    client.put_public_access_block.assert_called_once()
    sent = json.loads(client.put_bucket_policy.call_args.kwargs["Policy"])
    assert sent == generate_public_read_policy("my-bucket", ["dev", "test"])
