"""Bucket policies: generate, apply, read, and allow public access."""

import json
import logging

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def generate_public_read_policy(bucket):
    """Return a bucket policy (as a dict) that lets anyone read all objects in the bucket."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicReadGetObject",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/*",
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
