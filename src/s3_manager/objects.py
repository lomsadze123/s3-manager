"""Object operations: uploading, listing, inspecting, moving, deleting and restoring files, and setting object access."""

import io
import logging
import math
import mimetypes
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import magic
import requests
from botocore.exceptions import ClientError

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
UPLOAD_METHODS = ("auto", "small", "large", "upload_file", "upload_fileobj")
UNKNOWN_FOLDER = "unknown"

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

# Used by type_folder. For these types python-magic can't say more than "some text" or "some
# bytes", so the file's own extension decides the folder (data.csv is detected as text/plain).
GENERIC_MIME_TYPES = ("text/plain", "application/octet-stream", "inode/x-empty")
# Detected types the mimetypes module has no extension for.
MIME_TYPE_FOLDERS = {
    "image/x-ms-bmp": "bmp",
    "text/x-python": "py",
    "text/x-script.python": "py",
}


def upload_file(
    client,
    path,
    bucket,
    key=None,
    method="auto",
    threshold=DEFAULT_MULTIPART_THRESHOLD,
    part_size=DEFAULT_PART_SIZE,
    validate_mime=False,
    by_type=False,
):
    """Upload a local file to a bucket. Returns (key, method) with the method that was used.

    method:
        "small"          - one put_object request (upload_small_file)
        "large"          - multipart upload in parts of part_size bytes (upload_large_file)
        "auto"           - "large" if the file is at least threshold bytes, otherwise "small"
        "upload_file"    - boto3's managed upload_file, given the file path (upload_managed_file)
        "upload_fileobj" - boto3's managed upload_fileobj, given an open file (upload_file_object)

    If no key is given, the file name is used. The Content-Type comes from the file extension,
    or from the file content if the extension is unknown. With validate_mime=True the type is
    always detected from the content and only the allowed media types are accepted (see
    validate_media_type), so nothing is uploaded if the file is e.g. HTML renamed to .jpg.
    With by_type=True the key is put into a folder named after the type detected from the
    content (see type_folder): fake.jpg that is really a PNG -> png/fake.jpg.
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

    if by_type:
        folder = type_folder(detected_type, path.name)
        logger.info("Detected type %s, uploading into folder '%s/'.", detected_type, folder)
        key = f"{folder}/{key}"

    if method == "auto":
        method = "large" if size >= threshold else "small"
    logger.info("File is %.1f MiB, using the '%s' upload method.", size / MIB, method)

    if method == "small":
        upload_small_file(client, path, bucket, key, content_type)
    elif method == "large":
        upload_large_file(client, path, bucket, key, content_type, part_size=part_size)
    elif method == "upload_file":
        upload_managed_file(client, path, bucket, key, content_type)
    else:
        upload_file_object(client, path, bucket, key, content_type)
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


def upload_managed_file(client, path, bucket, key, content_type=DEFAULT_CONTENT_TYPE):
    """Upload a file with boto3's upload_file, which takes the path of the file.

    This is a "managed transfer": boto3 itself decides between one request and a multipart
    upload (from 8 MiB by default), sends the parts in several threads and retries failed ones.
    Extra settings like the Content-Type go in ExtraArgs.
    """
    logger.info("Uploading '%s' to s3://%s/%s with upload_file...", path, bucket, key)
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
    logger.info("Upload finished.")


def upload_file_object(client, path, bucket, key, content_type=DEFAULT_CONTENT_TYPE):
    """Upload a file with boto3's upload_fileobj, which takes an open file in binary mode.

    Same managed transfer as upload_file, but it reads from any file-like object, so it also
    works for data that is not on disk (e.g. io.BytesIO, see download_file_and_upload_to_s3).
    """
    logger.info("Uploading '%s' to s3://%s/%s with upload_fileobj...", path, bucket, key)
    with open(path, "rb") as file:
        client.upload_fileobj(file, bucket, key, ExtraArgs={"ContentType": content_type})
    logger.info("Upload finished.")


def detect_mime_type(content):
    """Return the MIME type of the given bytes, detected from their content (magic numbers)."""
    # libmagic only needs the start of the file to recognise its type.
    return magic.from_buffer(content[:2048], mime=True)


def type_folder(content_type, file_name):
    """Return the folder name for a file: the extension of its detected MIME type, without the dot.

    image/png -> "png", application/pdf -> "pdf", video/mp4 -> "mp4". For generic types
    (text/plain, application/octet-stream, empty file) the file's own extension is used instead,
    so data.csv -> "csv". If no extension is found at all, returns UNKNOWN_FOLDER.
    """
    if content_type in GENERIC_MIME_TYPES:
        return extension_folder(Path(file_name).name) or UNKNOWN_FOLDER

    folder = MIME_TYPE_FOLDERS.get(content_type)
    if folder is None:
        extension = mimetypes.guess_extension(content_type or "")
        folder = extension[1:].lower() if extension else UNKNOWN_FOLDER
    return folder


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


def get_objects(client, bucket, prefix=""):
    """Return every object in the bucket (optionally only under a folder), sorted by key.

    Each item is {"key", "size", "modified"}. list_objects_v2 returns at most 1000 keys per
    request, so a paginator is used to get all of them. An empty bucket gives an empty list.
    """
    logger.info("Listing objects in s3://%s/%s...", bucket, prefix)
    items = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.lstrip("/")):
        for item in page.get("Contents", []):
            items.append({"key": item["Key"], "size": item["Size"], "modified": item["LastModified"]})
    return sorted(items, key=lambda item: item["key"])


def get_object_info(client, bucket, key):
    """Return the size and Content-Type of an object, or None if it does not exist."""
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None
        raise
    return {"size": response["ContentLength"], "content_type": response.get("ContentType")}


def delete_object(client, bucket, key):
    """Delete one object.

    S3 answers with success even if the key does not exist, so check with
    get_object_info() first. In a versioned bucket this only adds a delete marker.
    """
    logger.info("Deleting s3://%s/%s...", bucket, key)
    client.delete_object(Bucket=bucket, Key=key)
    logger.info("Object deleted.")


def list_object_versions(client, bucket, key):
    """Return all versions of one object, newest first.

    Each item is {"version_id", "created", "size", "is_latest"}. A version can't be changed
    after it is written, so its LastModified date is the date the version was created.
    Delete markers (left by deleting an object in a versioned bucket) are not versions with
    content, so they are skipped. In a bucket without versioning there is at most one version,
    with the id "null". An empty list means the object does not exist.
    """
    logger.info("Listing versions of s3://%s/%s...", bucket, key)
    versions = []
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=key):
        for version in page.get("Versions", []):
            # Prefix also matches other keys that start with the same text, e.g. photo.jpg.bak.
            if version["Key"] != key:
                continue
            versions.append({
                "version_id": version["VersionId"],
                "created": version["LastModified"],
                "size": version["Size"],
                "is_latest": version["IsLatest"],
            })
    versions.sort(key=lambda version: version["created"], reverse=True)
    return versions


def restore_previous_version(client, bucket, key):
    """Upload the second-to-last version of an object again, as a new (latest) version.

    S3 copies the old version onto the same key with copy_object, so nothing is downloaded and
    the content and metadata (e.g. Content-Type) stay the same. All older versions are kept.
    copy_object works for objects up to 5 GiB.

    Returns (source_version, new_version_id), where source_version is the item from
    list_object_versions() that was copied.
    """
    versions = list_object_versions(client, bucket, key)
    if len(versions) < 2:
        raise ValueError(
            f"s3://{bucket}/{key} has {len(versions)} version(s), at least 2 are needed. "
            f"Enable versioning with 'bucket versioning {bucket} --enable' and upload the file again."
        )

    previous = versions[1]
    return previous, copy_version_as_latest(client, bucket, key, previous["version_id"])


def rollback_to_version(client, bucket, key, version_id):
    """Upload a chosen version of an object again, as a new (latest) version.

    Same as restore_previous_version, but for any version id (see 'object file -ver').
    Returns (source_version, new_version_id). Raises ValueError if the version does not exist
    or is already the latest one.
    """
    versions = list_object_versions(client, bucket, key)
    source = next((version for version in versions if version["version_id"] == version_id), None)
    if source is None:
        raise ValueError(
            f"s3://{bucket}/{key} has no version '{version_id}'. "
            f"List the versions with 'object file {bucket} {key} -ver'."
        )
    if source["is_latest"]:
        raise ValueError(f"Version '{version_id}' is already the latest version of s3://{bucket}/{key}.")

    return source, copy_version_as_latest(client, bucket, key, version_id)


def copy_version_as_latest(client, bucket, key, version_id):
    """Copy an old version of an object onto the same key and return the new version id."""
    logger.info("Copying version %s of s3://%s/%s as a new version...", version_id, bucket, key)
    response = client.copy_object(
        Bucket=bucket,
        Key=key,
        CopySource={"Bucket": bucket, "Key": key, "VersionId": version_id},
    )
    logger.info("New version created.")
    return response.get("VersionId")


def extension_folder(key):
    """Return the folder name for a key: its extension in lowercase, without the dot.

    Returns None for keys that are already inside a folder (they contain "/") and for keys
    without an extension (e.g. "README" or ".env"), because those are not moved.
    """
    if "/" in key:
        return None
    suffix = Path(key).suffix
    return suffix[1:].lower() or None


def move_object(client, bucket, key, new_key):
    """Move an object to a new key in the same bucket.

    S3 has no "move" or "rename": the object is copied to the new key with copy_object (done
    inside S3, nothing is downloaded, Content-Type is kept) and the old key is deleted.
    copy_object works for objects up to 5 GiB.
    """
    logger.info("Moving s3://%s/%s to %s...", bucket, key, new_key)
    client.copy_object(Bucket=bucket, Key=new_key, CopySource={"Bucket": bucket, "Key": key})
    client.delete_object(Bucket=bucket, Key=key)


def organize_by_extension(client, bucket, include_unknown=False):
    """Move every file in the bucket root into a folder named after its extension.

    image.jpg -> jpg/image.jpg, demo.csv -> csv/demo.csv. Objects already inside a folder are
    left alone, so running it again changes nothing. Files without an extension are skipped,
    or moved to unknown/ with include_unknown=True (README -> unknown/README).
    Returns a Counter with the number of moved files per extension, e.g. {"csv": 2, "jpg": 1}.

    If a key like csv/demo.csv already exists it is overwritten. In a versioned bucket the old
    key only gets a delete marker, its versions are kept.
    """
    # Delimiter="/" makes S3 return only root-level keys in "Contents" (keys inside folders are
    # grouped under "CommonPrefixes"). All keys are listed first, so the moved ones are not
    # listed again while moving.
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Delimiter="/"):
        keys.extend(item["Key"] for item in page.get("Contents", []))

    counts = Counter()
    for key in keys:
        folder = extension_folder(key) or (UNKNOWN_FOLDER if include_unknown else None)
        if folder is None:
            logger.info("Skipping '%s': it has no extension.", key)
            continue
        move_object(client, bucket, key, f"{folder}/{key}")
        counts[folder] += 1
    return counts
