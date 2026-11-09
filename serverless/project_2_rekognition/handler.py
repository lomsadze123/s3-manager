"""Lecture 6, Project 2: analyse images and videos uploaded to S3 with AWS Rekognition.

start_processing_media (S3 trigger): images are labelled right away (DetectLabels) and saved to
DynamoDB. For videos a Rekognition job is started (StartLabelDetection), which runs for a while.
handle_label_detection (SNS trigger): Rekognition's "job done" message arrives here, and the
video's labels are read and saved to DynamoDB.

Deployed by serverless.yml on its own, so only boto3 (already in the Lambda runtime) is used.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote_plus

import boto3

IMAGE_EXTENSIONS = {"jpeg", "jpg", "png"}
VIDEO_EXTENSIONS = {"mp4"}

rekognition = boto3.client("rekognition")
dynamodb = boto3.resource("dynamodb")


def media_extension(key):
    """Lower-case extension of an object key: 'photos/Cat.JPG' -> 'jpg', 'notes' -> ''."""
    name = key.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def start_video_label_detection(bucket, key):
    """Start a Rekognition job for a video. Rekognition publishes to SNS when it is done."""
    response = rekognition.start_label_detection(
        Video={"S3Object": {"Bucket": bucket, "Name": key}},
        NotificationChannel={
            "SNSTopicArn": os.environ["REKOGNITION_SNS_TOPIC_ARN"],
            "RoleArn": os.environ["REKOGNITION_ROLE_ARN"],
        },
    )
    print(f"Rekognition job {response['JobId']} started for s3://{bucket}/{key}")
    return response["JobId"]


# https://docs.aws.amazon.com/rekognition/latest/dg/labels-detect-labels-image.html
def get_image_labels(bucket, key):
    """Labels of an image in S3 (at most 10, the most confident first)."""
    return rekognition.detect_labels(
        Image={"S3Object": {"Bucket": bucket, "Name": key}},
        MaxLabels=10,
    )


def get_video_labels(job_id):
    """Every label of a finished video job. The results come in pages, so read all of them."""
    response = rekognition.get_label_detection(JobId=job_id, SortBy="TIMESTAMP")
    next_token = response.pop("NextToken", None)

    while next_token:
        next_page = rekognition.get_label_detection(
            JobId=job_id, SortBy="TIMESTAMP", NextToken=next_token
        )
        response["Labels"].extend(next_page["Labels"])
        next_token = next_page.get("NextToken")

    return response


def summarize_video_labels(labels):
    """Turn the per-frame video labels into one entry per label name.

    A video gets a label for every moment it is seen (with bounding boxes), which quickly goes
    over DynamoDB's 400 KB item limit. The summary keeps, per label: the highest confidence,
    how many times it was seen, and when it was seen first and last (milliseconds into the video).
    """
    summary = {}
    for entry in labels:
        label = entry["Label"]
        name = label["Name"]
        confidence = label.get("Confidence", 0.0)
        timestamp = entry.get("Timestamp", 0)

        if name not in summary:
            summary[name] = {
                "Name": name,
                "Confidence": confidence,
                "Occurrences": 0,
                "FirstSeenMs": timestamp,
                "LastSeenMs": timestamp,
            }
        found = summary[name]
        found["Confidence"] = max(found["Confidence"], confidence)
        found["Occurrences"] += 1
        found["FirstSeenMs"] = min(found["FirstSeenMs"], timestamp)
        found["LastSeenMs"] = max(found["LastSeenMs"], timestamp)

    return sorted(summary.values(), key=lambda item: (-item["Confidence"], item["Name"]))


def make_item(data):
    """Make Rekognition's answer storable: DynamoDB doesn't accept float, only Decimal."""
    if isinstance(data, dict):
        return {key: make_item(value) for key, value in data.items()}
    if isinstance(data, list):
        return [make_item(value) for value in data]
    if isinstance(data, float):
        return Decimal(str(data))
    return data


def put_labels_in_db(data, media_name, media_bucket):
    """Save Rekognition's answer for one file as a new DynamoDB item and return its id."""
    item = dict(data)
    item.pop("ResponseMetadata", None)

    if "JobStatus" in item:
        del item["JobStatus"]
        item["mediaType"] = "Video"
    else:
        item["mediaType"] = "Image"

    item["mediaName"] = media_name
    item["mediaBucket"] = media_bucket
    item["id"] = str(uuid.uuid4())
    item["createdAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    table = dynamodb.Table(os.environ["DYNAMO_DB_TABLE"])
    table.put_item(Item=make_item(item))
    print(f"Saved {item['mediaType'].lower()} labels of s3://{media_bucket}/{media_name} as {item['id']}")
    return item["id"]


# Lambda events


def start_processing_media(event, context):
    """S3 trigger: label images now, start a Rekognition job for videos."""
    for record in event["Records"]:
        bucket_name = record["s3"]["bucket"]["name"]
        # keys arrive URL-encoded ("my photo.png" -> "my+photo.png"), so decode before using them
        object_key = unquote_plus(record["s3"]["object"]["key"])
        extension = media_extension(object_key)

        if extension in VIDEO_EXTENSIONS:
            start_video_label_detection(bucket_name, object_key)
        elif extension in IMAGE_EXTENSIONS:
            labels = get_image_labels(bucket_name, object_key)
            put_labels_in_db(labels, object_key, bucket_name)
        else:
            print(f"Skipped s3://{bucket_name}/{object_key}: not a .jpeg, .jpg, .png or .mp4 file")


def handle_label_detection(event, context):
    """SNS trigger: Rekognition finished a video job, so save its labels."""
    for record in event["Records"]:
        message = json.loads(record["Sns"]["Message"])
        job_id = message["JobId"]
        s3_object = message["Video"]["S3ObjectName"]
        s3_bucket = message["Video"]["S3Bucket"]

        if message.get("Status") != "SUCCEEDED":
            print(f"Rekognition job {job_id} for {s3_object} ended with status {message.get('Status')}")
            continue

        response = get_video_labels(job_id)
        response["Labels"] = summarize_video_labels(response.get("Labels", []))
        put_labels_in_db(response, s3_object, s3_bucket)
