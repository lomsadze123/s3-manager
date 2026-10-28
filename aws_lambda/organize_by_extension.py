"""AWS Lambda function: move every new file in the bucket root into a folder named after its extension.

S3 calls lambda_handler for every new object (the trigger set up by `s3-manager organize deploy`).
demo.csv -> csv/demo.csv, photo.JPG -> jpg/photo.JPG. It is the Lambda version of
`s3-manager bucket organize NAME -ext`, which does the same for all files at once.

S3 has no "move": the object is copied to the new key and the old key is deleted. The copy is a
new object too, so S3 calls the function again for csv/demo.csv. Keys that are already inside a
folder (they contain "/") are skipped, so that second call does nothing and there is no loop.

This file is deployed on its own, so it only uses the standard library and boto3.

Environment variables of the function:
    MOVE_UNKNOWN - "true": files without an extension go to unknown/ (README -> unknown/README).
                   Anything else (default): they are left where they are.
"""

import json
import logging
import os
from pathlib import PurePosixPath
from urllib.parse import unquote_plus

import boto3

MOVE_UNKNOWN = os.environ.get("MOVE_UNKNOWN", "false").lower() == "true"
UNKNOWN_FOLDER = "unknown"

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Created once per Lambda container and reused by every invocation.
s3_client = boto3.client("s3")


def extension_folder(key):
    """Return the folder name for a key: its extension in lowercase, without the dot.

    Returns None for keys that are already inside a folder (they contain "/") and for keys
    without an extension (e.g. "README" or ".env"). Same rule as objects.extension_folder().
    """
    if "/" in key:
        return None
    suffix = PurePosixPath(key).suffix
    return suffix[1:].lower() or None


def move_object(bucket, key, new_key):
    """Copy the object to the new key inside S3 (nothing is downloaded) and delete the old key."""
    logger.info("Moving s3://%s/%s to %s...", bucket, key, new_key)
    s3_client.copy_object(Bucket=bucket, Key=new_key, CopySource={"Bucket": bucket, "Key": key})
    s3_client.delete_object(Bucket=bucket, Key=key)


def lambda_handler(event, _context):
    """Handle an S3 "object created" event: move each new root-level file into its extension folder."""
    moved = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # Keys in S3 events are URL-encoded: "my notes.txt" arrives as "my+notes.txt".
        key = unquote_plus(record["s3"]["object"]["key"])

        if "/" in key:
            logger.info("Skipping s3://%s/%s, it is already in a folder.", bucket, key)
            continue
        folder = extension_folder(key) or (UNKNOWN_FOLDER if MOVE_UNKNOWN else None)
        if folder is None:
            logger.info("Skipping s3://%s/%s, it has no extension.", bucket, key)
            continue

        new_key = f"{folder}/{key}"
        move_object(bucket, key, new_key)
        moved.append({"from": key, "to": new_key})

    return {"statusCode": 200, "body": json.dumps({"moved": moved})}
