"""Bucket lifecycle rules: delete objects automatically after a number of days."""

import logging

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

DEFAULT_EXPIRATION_DAYS = 120


def create_expiration_rule(days=DEFAULT_EXPIRATION_DAYS, prefix=""):
    """Return a lifecycle rule (as a dict) that deletes objects `days` days after they were created.

    With a prefix (e.g. "logs/") only objects under that folder are deleted. An empty prefix
    means every object in the bucket.
    """
    if days < 1:
        raise ValueError(f"Days must be at least 1, got {days}.")

    prefix = prefix.lstrip("/")
    return {
        "ID": f"expire-{prefix.strip('/') or 'all'}-after-{days}-days",
        "Status": "Enabled",
        "Filter": {"Prefix": prefix},
        "Expiration": {"Days": days},
    }


def set_lifecycle_policy(client, bucket, days=DEFAULT_EXPIRATION_DAYS, prefix=""):
    """Apply a lifecycle policy that deletes objects `days` days after creation.

    put_bucket_lifecycle_configuration replaces the bucket's whole lifecycle configuration,
    so any rules that were there before are removed.
    """
    rule = create_expiration_rule(days, prefix)
    logger.info("Setting lifecycle rule '%s' on '%s'...", rule["ID"], bucket)
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket, LifecycleConfiguration={"Rules": [rule]}
    )
    logger.info("Lifecycle policy applied.")
    return rule


def read_lifecycle_policy(client, bucket):
    """Return the bucket's lifecycle rules, or None if the bucket has no lifecycle configuration."""
    logger.info("Reading lifecycle policy of '%s'...", bucket)
    try:
        response = client.get_bucket_lifecycle_configuration(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
            return None
        raise
    return response["Rules"]


def delete_lifecycle_policy(client, bucket):
    """Remove all lifecycle rules from the bucket."""
    logger.info("Deleting lifecycle policy of '%s'...", bucket)
    client.delete_bucket_lifecycle(Bucket=bucket)
    logger.info("Lifecycle policy deleted.")
