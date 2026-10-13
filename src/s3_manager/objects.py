"""Object operations: uploading files and setting object access."""

import io
import logging
import math
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import magic
import requests

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"
ALLOWED_ACLS = ("private", "public-read")

# Upload sizes. S3 limits: one put_object request can send at most 5 GiB, every multipart part
# except the last must be at least 5 MiB, and an upload can have at most 10,000 parts.
MIB = 1024 * 1024
MAX_SMALL_UPLOAD_SIZE = 5 * 1024 * MIB
MIN_PART_SIZE = 5 * MIB
MAX_PARTS = 10_000
DEFAULT_PART_SIZE = 10 * MIB
DEFAULT_MULTIPART_THRESHOLD = 100 * MIB  # AWS recommends multipart uploads from about 100 MB
UPLOAD_METHODS = ("auto", "small", "large")

# File types accepted by download_file_and_upload_to_s3 and by upload_file(validate_mime=True):
# detected MIME type -> valid extensions. The first extension is added to keys that have none.
ALLOWED_MEDIA_TYPES = {
    "image/bmp": (".bmp",),
    "image/x-ms-bmp": (".bmp",),  # older libmagic versions use this name for BMP
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
    "video/mp4": (".mp4",),
}
ALLOWED_MEDIA_EXTENSIONS = ".bmp, .jpg, .jpeg, .png, .webp, .mp4"


def upload_file(
    client,
    path,
    bucket,
    key=None,
    method="auto",
    threshold=DEFAULT_MULTIPART_THRESHOLD,
    part_size=DEFAULT_PART_SIZE,
    validate_mime=False,
):
    """Upload a local file to a bucket. Returns (key, method) with the method that was used.

    method:
        "small" - one put_object request (upload_small_file)
        "large" - multipart upload in parts of part_size bytes (upload_large_file)
        "auto"  - "large" if the file is at least threshold bytes, otherwise "small"

    If no key is given, the file name is used. The Content-Type comes from the file extension,
    or from the file content if the extension is unknown. With validate_mime=True the type is
    always detected from the content and only the allowed media types are accepted (see
    validate_media_type), so nothing is uploaded if the file is e.g. HTML renamed to .jpg.
    """
    if method not in UPLOAD_METHODS:
        raise ValueError(f"Upload method must be one of {', '.join(UPLOAD_METHODS)}, got '{method}'.")

    path = Path(path)
    key = key or path.name
    size = path.stat().st_size

    with open(path, "rb") as file:
        detected_type = detect_mime_type(file.read(2048))

    if validate_mime:
        key = validate_media_type(detected_type, key)
        content_type = detected_type
    else:
        content_type = mimetypes.guess_type(path.name)[0] or detected_type or DEFAULT_CONTENT_TYPE

    if method == "auto":
        method = "large" if size >= threshold else "small"
    logger.info("File is %.1f MiB, using the '%s' upload method.", size / MIB, method)

    if method == "small":
        upload_small_file(client, path, bucket, key, content_type)
    else:
        upload_large_file(client, path, bucket, key, content_type, part_size=part_size)
    return key, method


def upload_small_file(client, path, bucket, key, content_type=DEFAULT_CONTENT_TYPE):
    """Upload a file with a single put_object request.

    The whole file is read into memory and sent at once, which is the simplest and fastest way
    for small files. S3 accepts at most 5 GiB per request; bigger files need upload_large_file.
    """
    path = Path(path)
    if path.stat().st_size > MAX_SMALL_UPLOAD_SIZE:
        raise ValueError("Files larger than 5 GiB can't be uploaded in one request. Use the 'large' method.")

    logger.info("Uploading '%s' to s3://%s/%s in one request...", path, bucket, key)
    client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType=content_type)
    logger.info("Upload finished.")


def upload_large_file(client, path, bucket, key, content_type=DEFAULT_CONTENT_TYPE, part_size=DEFAULT_PART_SIZE):
    """Upload a file with a multipart upload, reading and sending it part by part.

    1. create_multipart_upload starts the upload and returns an UploadId.
    2. upload_part sends each part (only one part is in memory at a time). S3 returns an ETag
       for every part.
    3. complete_multipart_upload gives S3 the list of part numbers and ETags, and S3 joins the
       parts into one object.

    If anything fails, abort_multipart_upload is called. Otherwise the parts that were already
    sent would stay in the bucket, invisible but still billed.
    """
    path = Path(path)
    if part_size < MIN_PART_SIZE:
        raise ValueError("Part size must be at least 5 MiB (S3 minimum).")

    size = path.stat().st_size
    total_parts = max(1, math.ceil(size / part_size))
    if total_parts > MAX_PARTS:
        raise ValueError(f"The file would need {total_parts} parts, S3 allows {MAX_PARTS}. Use a bigger part size.")

    logger.info(
        "Starting multipart upload of '%s' to s3://%s/%s (%d parts of %.0f MiB)...",
        path, bucket, key, total_parts, part_size / MIB,
    )
    upload_id = client.create_multipart_upload(Bucket=bucket, Key=key, ContentType=content_type)["UploadId"]

    try:
        parts = []
        with open(path, "rb") as file:
            for part_number in range(1, total_parts + 1):
                response = client.upload_part(
                    Bucket=bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=file.read(part_size),
                )
                parts.append({"ETag": response["ETag"], "PartNumber": part_number})
                logger.info("Uploaded part %d/%d.", part_number, total_parts)

        client.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
        )
    except Exception:
        logger.warning("Upload failed, aborting the multipart upload so no parts are left behind.")
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise

    logger.info("Upload finished.")


def detect_mime_type(content):
    """Return the MIME type of the given bytes, detected from their content (magic numbers)."""
    # libmagic only needs the start of the file to recognise its type.
    return magic.from_buffer(content[:2048], mime=True)


def validate_media_type(content_type, key):
    """Check a detected MIME type against the allowed media types and return the final key.

    Raises ValueError if the type is not allowed or the key's extension doesn't match it.
    A key without an extension gets the right one added.
    """
    extensions = ALLOWED_MEDIA_TYPES.get(content_type)
    if extensions is None:
        raise ValueError(
            f"Unsupported file type '{content_type}'. Allowed: {ALLOWED_MEDIA_EXTENSIONS}."
        )

    suffix = Path(key).suffix.lower()
    if not suffix:
        return key + extensions[0]
    if suffix not in extensions:
        raise ValueError(
            f"Key '{key}' ends with '{suffix}', but the file is {content_type}. "
            f"Use one of: {', '.join(extensions)}."
        )
    return key


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

    key = validate_media_type(content_type, key)

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
