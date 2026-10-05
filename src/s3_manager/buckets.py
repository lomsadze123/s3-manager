"""Bucket operations: list, create, delete, check existence."""

import logging

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def list_buckets(client):
    """Return the names of all buckets in the account."""
    logger.info("Listing buckets...")
    response = client.list_buckets()
    return [bucket["Name"] for bucket in response["Buckets"]]


def create_bucket(client, name, enable_acl=False):
    """Create a bucket in the client's region.

    If enable_acl is True, the bucket is created with ObjectOwnership="ObjectWriter",
    so object ACLs (e.g. public-read) can be used on it.
    """
    region = client.meta.region_name
    params = {"Bucket": name}

    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}

    if enable_acl:
        params["ObjectOwnership"] = "ObjectWriter"

    logger.info("Creating bucket '%s' in region %s...", name, region)
    client.create_bucket(**params)
    logger.info("Bucket '%s' created successfully.", name)


def delete_bucket(client, name):
    """Delete a bucket. The bucket must be empty."""
    logger.info("Deleting bucket '%s'...", name)
    client.delete_bucket(Bucket=name)
    logger.info("Bucket '%s' deleted successfully.", name)


def bucket_exists(client, name):
    """Return True if the bucket exists and we can access it, False if it does not exist."""
    try:
        client.head_bucket(Bucket=name)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            return False
        raise
