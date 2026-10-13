import pytest

from s3_manager.lifecycle import (
    create_expiration_rule,
    delete_lifecycle_policy,
    read_lifecycle_policy,
    set_lifecycle_policy,
)
from tests.conftest import make_client_error


def test_create_expiration_rule_defaults_to_120_days_for_whole_bucket():
    assert create_expiration_rule() == {
        "ID": "expire-all-after-120-days",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "Expiration": {"Days": 120},
    }


def test_create_expiration_rule_with_prefix():
    rule = create_expiration_rule(30, prefix="/logs/")

    # S3 keys have no leading slash, so "/logs/" must become "logs/".
    assert rule["Filter"] == {"Prefix": "logs/"}
    assert rule["Expiration"] == {"Days": 30}
    assert rule["ID"] == "expire-logs-after-30-days"


def test_create_expiration_rule_rejects_zero_days():
    with pytest.raises(ValueError):
        create_expiration_rule(0)


def test_set_lifecycle_policy_sends_rule(client):
    set_lifecycle_policy(client, "my-bucket")

    client.put_bucket_lifecycle_configuration.assert_called_once_with(
        Bucket="my-bucket", LifecycleConfiguration={"Rules": [create_expiration_rule(120)]}
    )


def test_read_lifecycle_policy_returns_rules(client):
    client.get_bucket_lifecycle_configuration.return_value = {"Rules": [create_expiration_rule()]}

    assert read_lifecycle_policy(client, "my-bucket") == [create_expiration_rule()]


def test_read_lifecycle_policy_returns_none_without_configuration(client):
    client.get_bucket_lifecycle_configuration.side_effect = make_client_error("NoSuchLifecycleConfiguration")

    assert read_lifecycle_policy(client, "my-bucket") is None


def test_read_lifecycle_policy_raises_other_errors(client):
    client.get_bucket_lifecycle_configuration.side_effect = make_client_error("AccessDenied")

    with pytest.raises(Exception, match="AccessDenied"):
        read_lifecycle_policy(client, "my-bucket")


def test_delete_lifecycle_policy(client):
    delete_lifecycle_policy(client, "my-bucket")

    client.delete_bucket_lifecycle.assert_called_once_with(Bucket="my-bucket")
