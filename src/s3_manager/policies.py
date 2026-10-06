"""Bucket policies: generate, apply, read, and allow public access."""

import json
import logging

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def generate_public_read_policy(bucket, prefixes=None):
    """Return a bucket policy (as a dict) that lets anyone read objects in the bucket.

    Without prefixes the whole bucket is public. With prefixes (e.g. ["dev", "test"])
    only objects under those folders are public. S3 keys have no leading slash,
    so "/dev", "dev" and "dev/" all mean the same folder.
    """
    if prefixes:
        resource = [f"arn:aws:s3:::{bucket}/{prefix.strip('/')}/*" for prefix in prefixes]
    else:
        resource = f"arn:aws:s3:::{bucket}/*"

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicReadGetObject",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": resource,
            }
        ],
    }


def create_bucket_policy(client, bucket, policy=None):
    """Apply a bucket policy. Uses the public-read policy if none is given."""
    policy = policy or generate_public_read_policy(bucket)
    logger.info("Applying bucket policy to '%s'...", bucket)
    client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    logger.info("Bucket policy applied.")


def read_bucket_policy(client, bucket):
    """Return the bucket policy as a dict, or None if the bucket has no policy."""
    logger.info("Reading bucket policy of '%s'...", bucket)
    try:
        response = client.get_bucket_policy(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] == "NoSuchBucketPolicy":
            return None
        raise
    return json.loads(response["Policy"])


def ensure_bucket_policy(client, bucket, prefixes):
    """Apply a public-read policy for the given prefixes, unless the bucket already has a policy.

    Block Public Access is turned off first, otherwise AWS rejects the public policy
    with AccessDenied. Returns True if a policy was applied, False if one already existed.
    """
    if read_bucket_policy(client, bucket) is not None:
        logger.info("Bucket '%s' already has a policy, skipping.", bucket)
        return False

    disable_block_public_access(client, bucket)
    create_bucket_policy(client, bucket, generate_public_read_policy(bucket, prefixes))
    return True


def disable_block_public_access(client, bucket):
    """Turn off the bucket's Block Public Access settings so public policies/ACLs are allowed."""
    logger.info("Disabling Block Public Access for '%s'...", bucket)
    client.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False,
            "IgnorePublicAcls": False,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False,
        },
    )
