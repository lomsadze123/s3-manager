"""Creating the boto3 S3 client.

This is the only module that reads configuration from the environment.
"""

import logging
import os

import boto3
from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-east-1"


def init_client(region=None):
    """Create a boto3 S3 client using settings from the environment / .env file.

    region overrides AWS_REGION, e.g. to create a bucket in another region. A create_bucket
    request should be sent to the endpoint of the bucket's region, otherwise S3 can answer with
    IllegalLocationConstraintException, so the client is created for that region.

    Environment variables:
        AWS_ACCESS_KEY_ID      - access key (required unless using an AWS profile)
        AWS_SECRET_ACCESS_KEY  - secret key (required unless using an AWS profile)
        AWS_SESSION_TOKEN      - optional, only for temporary credentials (e.g. AWS Academy labs)
        AWS_REGION            - region, e.g. "eu-central-1" (default: us-east-1)
        S3_ENDPOINT_URL        - optional, for S3-compatible services (MinIO, LocalStack)
    """
    load_dotenv(find_dotenv(usecwd=True))

    region = region or os.getenv("AWS_REGION") or DEFAULT_REGION
    endpoint_url = os.getenv("S3_ENDPOINT_URL") or None

    logger.debug("Creating S3 client (region=%s, endpoint=%s)", region, endpoint_url or "AWS default")

    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN") or None,
        region_name=region,
        endpoint_url=endpoint_url,
    )
