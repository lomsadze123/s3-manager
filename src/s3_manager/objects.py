"""Object operations: uploading files and setting object access."""

import io
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import magic
import requests

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"
ALLOWED_ACLS = ("private", "public-read")

# File types accepted by download_file_and_upload_to_s3: detected MIME type -> valid extensions.
# The first extension is added to keys that have none.
ALLOWED_DOWNLOAD_TYPES = {
    "image/bmp": (".bmp",),
    "image/x-ms-bmp": (".bmp",),  # older libmagic versions use this name for BMP
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
    "video/mp4": (".mp4",),
}
ALLOWED_DOWNLOAD_EXTENSIONS = ".bmp, .jpg, .jpeg, .png, .webp, .mp4"


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


def detect_mime_type(content):
    """Return the MIME type of the given bytes, detected from their content (magic numbers)."""
    # libmagic only needs the start of the file to recognise its type.
    return magic.from_buffer(content[:2048], mime=True)


def download_file_and_upload_to_s3(client, url, bucket, key=None):
    """Download a file from a URL and upload it to a bucket. Returns the object key.

    Only images (.bmp, .jpg, .jpeg, .png, .webp) and .mp4 videos are accepted. The type is
    detected from the file content with python-magic, not from the URL or the server's
    Content-Type header, because those can be wrong.

    If no key is given, the file name from the URL path is used. A key without an extension
    gets the right one added; a key with a wrong extension is rejected.
    The file is kept in memory, so this is meant for small and medium files.
    """
    key = key or Path(urlparse(url).path).name or "downloaded-file"

    logger.info("Downloading %s...", url)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    content = response.content

    content_type = detect_mime_type(content)
    logger.info("Downloaded %d bytes, detected type: %s.", len(content), content_type)

    extensions = ALLOWED_DOWNLOAD_TYPES.get(content_type)
    if extensions is None:
        raise ValueError(
            f"Unsupported file type '{content_type}'. Allowed: {ALLOWED_DOWNLOAD_EXTENSIONS}."
        )

    suffix = Path(key).suffix.lower()
    if not suffix:
        key += extensions[0]
    elif suffix not in extensions:
        raise ValueError(
            f"Key '{key}' ends with '{suffix}', but the file is {content_type}. "
            f"Use one of: {', '.join(extensions)}."
        )

    logger.info("Uploading to s3://%s/%s...", bucket, key)
    client.upload_fileobj(
        io.BytesIO(response.content), bucket, key, ExtraArgs={"ContentType": content_type}
    )
    logger.info("Upload finished.")
    return key


def set_object_access_policy(client, bucket, key, acl="public-read"):
    """Set a canned ACL ("private" or "public-read") on a single object.

    Only works on buckets created with ACLs enabled (ObjectOwnership="ObjectWriter")
    and with Block Public Access turned off (for "public-read").
    """
    if acl not in ALLOWED_ACLS:
        raise ValueError(f"ACL must be one of {', '.join(ALLOWED_ACLS)}, got '{acl}'.")

    logger.info("Setting ACL '%s' on s3://%s/%s...", acl, bucket, key)
    client.put_object_acl(Bucket=bucket, Key=key, ACL=acl)
    logger.info("ACL updated.")
