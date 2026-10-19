"""Static website hosting: turn a bucket into a website and upload an HTML page or a built React app."""

import logging
import mimetypes
from pathlib import Path

from botocore.exceptions import ClientError

from s3_manager import buckets, objects, policies

logger = logging.getLogger(__name__)

INDEX_DOCUMENT = "index.html"

# mimetypes does not know these extensions on every Python version / OS, or names them differently
# (older Pythons say application/javascript). Browsers refuse to run a module script that is not
# served with a JavaScript type, so a React app would show a blank page.
WEBSITE_CONTENT_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
# Types that are text, so the browser should read them as UTF-8 (names like "ბექა" stay readable).
UTF8_CONTENT_TYPES = ("text/javascript", "application/json", "application/manifest+json", "image/svg+xml")

HTML_CACHE_CONTROL = "no-cache"  # the browser asks S3 every time, so a new deploy shows up at once
ASSET_CACHE_CONTROL = "public, max-age=86400"  # one day; Vite puts a hash in asset file names

# Older regions use "s3-website-REGION" in the website endpoint, newer ones "s3-website.REGION".
# https://docs.aws.amazon.com/general/latest/gr/s3.html#s3_website_region_endpoints
DASH_WEBSITE_REGIONS = (
    "us-east-1",
    "us-west-1",
    "us-west-2",
    "eu-west-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-northeast-1",
    "sa-east-1",
    "us-gov-west-1",
)


def set_bucket_website_policy(client, bucket, switch=True, error_document=None):
    """Turn static website hosting on (switch=True) or off (switch=False) for a bucket.

    S3 then answers requests to the website endpoint with index.html for "/" and for every
    folder. error_document is the page shown when a key does not exist; a single page app
    (React) uses index.html for it, so the app can show its own "not found" page.
    """
    if not switch:
        logger.info("Turning off static website hosting for '%s'...", bucket)
        client.delete_bucket_website(Bucket=bucket)
        logger.info("Static website hosting turned off.")
        return

    configuration = {"IndexDocument": {"Suffix": INDEX_DOCUMENT}}
    if error_document:
        configuration["ErrorDocument"] = {"Key": error_document}
    logger.info("Turning on static website hosting for '%s'...", bucket)
    client.put_bucket_website(Bucket=bucket, WebsiteConfiguration=configuration)
    logger.info("Static website hosting turned on.")


def read_bucket_website(client, bucket):
    """Return {"index": ..., "error": ...} of the website configuration, or None if hosting is off."""
    logger.info("Reading website configuration of '%s'...", bucket)
    try:
        response = client.get_bucket_website(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] == "NoSuchWebsiteConfiguration":
            return None
        raise
    return {
        "index": response.get("IndexDocument", {}).get("Suffix"),
        "error": response.get("ErrorDocument", {}).get("Key"),
    }


def website_url(bucket, region):
    """Return the public website endpoint of a bucket. It is HTTP only; S3 websites have no HTTPS."""
    separator = "-" if region in DASH_WEBSITE_REGIONS else "."
    return f"http://{bucket}.s3-website{separator}{region}.amazonaws.com"


def website_content_type(path):
    """Return the Content-Type for a website file, mostly from its extension.

    For web files the extension is more reliable than the content: python-magic sees CSS and
    JavaScript as text/plain, and the browser would then ignore them. Only files with an
    unknown extension are detected from their content.
    """
    path = Path(path)
    content_type = WEBSITE_CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
    if content_type is None:
        with open(path, "rb") as file:
            content_type = objects.detect_mime_type(file.read(2048))
    if content_type.startswith("text/") or content_type in UTF8_CONTENT_TYPES:
        content_type += "; charset=utf-8"
    return content_type


def cache_control(key):
    """Return the Cache-Control header for a website file: HTML is never cached, assets for a day."""
    return HTML_CACHE_CONTROL if key.lower().endswith((".html", ".htm")) else ASSET_CACHE_CONTROL


def collect_site_files(path):
    """Return (local file, key) pairs for a file or for every file in a folder.

    A single file keeps its name as the key. For a folder the keys are relative to it, so
    dist/assets/app.js becomes assets/app.js. Hidden files like .DS_Store are skipped.
    """
    path = Path(path)
    if path.is_file():
        return [(path, path.name)]

    files = []
    for file in sorted(path.rglob("*")):
        relative = file.relative_to(path)
        if file.is_file() and not any(part.startswith(".") for part in relative.parts):
            files.append((file, relative.as_posix()))
    return files


def static_web_page_file(client, bucket, path, prefix=""):
    """Upload a website file or a whole folder (keeping its subfolders) to a bucket.

    Every file gets the right Content-Type and Cache-Control, so the browser shows the page
    instead of downloading it. Returns the uploaded keys.
    """
    if not buckets.bucket_exists(client, bucket):
        raise ValueError(f"Bucket '{bucket}' does not exist.")

    prefix = prefix.strip("/")
    keys = []
    for file, key in collect_site_files(path):
        key = f"{prefix}/{key}" if prefix else key
        content_type = website_content_type(file)
        logger.info("Uploading %s to s3://%s/%s (%s)...", file, bucket, key, content_type)
        client.upload_file(
            str(file),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type, "CacheControl": cache_control(key)},
        )
        keys.append(key)
    return keys


def check_site_folder(path):
    """Raise ValueError if the path is not a website: a website needs index.html at the top."""
    path = Path(path)
    if path.is_file():
        if path.name != INDEX_DOCUMENT:
            raise ValueError(f"A website needs an {INDEX_DOCUMENT}, got '{path.name}'. Rename the file.")
        return

    if not (path / INDEX_DOCUMENT).is_file():
        message = f"No {INDEX_DOCUMENT} in '{path}'."
        if (path / "package.json").is_file():
            message += " React project? Run 'npm run build' first and pass the dist/ folder."
        raise ValueError(message)


def host_static_website(client, bucket, path, spa=False):
    """Host a file or a folder as a public website in one step. Returns (uploaded keys, URL).

    Creates the bucket if needed, turns off Block Public Access, applies a public-read policy
    for the whole bucket (this replaces an existing bucket policy), turns on website hosting
    and uploads the files. With spa=True missing keys also get index.html (single page apps).
    """
    check_site_folder(path)

    buckets.ensure_bucket(client, bucket)
    policies.disable_block_public_access(client, bucket)
    policies.create_bucket_policy(client, bucket, policies.generate_public_read_policy(bucket))
    set_bucket_website_policy(client, bucket, switch=True, error_document=INDEX_DOCUMENT if spa else None)
    keys = static_web_page_file(client, bucket, path)
    return keys, website_url(bucket, buckets.get_bucket_region(client, bucket))
