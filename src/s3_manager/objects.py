"""Object operations: uploading files and setting object access."""

import io
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"


def upload_file(client, path, bucket, key=None):
    """Upload a local file to a bucket and return the object key.

    If no key is given, the file name is used.
    """
    path = Path(path)
    key = key or path.name
    content_type = mimetypes.guess_type(path.name)[0] or DEFAULT_CONTENT_TYPE

    logger.info("Uploading '%s' to s3://%s/%s...", path, bucket, key)
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
    logger.info("Upload finished.")
    return key


def download_file_and_upload_to_s3(client, url, bucket, key=None):
    """Download a file from a URL and upload it to a bucket. Returns the object key.

    If no key is given, the file name from the URL path is used.
    The file is kept in memory, so this is meant for small and medium files.
    """
    key = key or Path(urlparse(url).path).name or "downloaded-file"

    logger.info("Downloading %s...", url)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", DEFAULT_CONTENT_TYPE)
    logger.info("Downloaded %d bytes (%s).", len(response.content), content_type)

    logger.info("Uploading to s3://%s/%s...", bucket, key)
    client.upload_fileobj(
        io.BytesIO(response.content), bucket, key, ExtraArgs={"ContentType": content_type}
    )
    logger.info("Upload finished.")
    return key
