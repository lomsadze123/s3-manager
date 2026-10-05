from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError


@pytest.fixture
def client():
    """A fake boto3 S3 client. It records every call instead of talking to AWS."""
    fake = MagicMock()
    fake.meta.region_name = "eu-central-1"
    return fake


def make_client_error(code):
    """Build the same kind of exception boto3 raises when AWS returns an error."""
    return ClientError({"Error": {"Code": code, "Message": "test error"}}, "TestOperation")
