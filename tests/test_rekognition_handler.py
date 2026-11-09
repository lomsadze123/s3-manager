"""Lecture 6, Project 2: the Lambda functions of serverless/project_2_rekognition (no AWS needed)."""

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HANDLER_FILE = (
    Path(__file__).resolve().parents[1] / "serverless" / "project_2_rekognition" / "handler.py"
)
TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:AmazonRekognition-rekognition-dev"
ROLE_ARN = "arn:aws:iam::123456789012:role/LabRole"

IMAGE_LABELS = {
    "Labels": [
        {"Name": "Cat", "Confidence": 99.5, "Instances": [{"BoundingBox": {"Width": 0.5}}], "Parents": []},
        {"Name": "Pet", "Confidence": 97.25, "Instances": [], "Parents": []},
    ],
    "LabelModelVersion": "3.0",
    "ResponseMetadata": {"HTTPStatusCode": 200},
}


@pytest.fixture
def handler(monkeypatch):
    """The Lambda module, loaded from its file, with fake Rekognition and DynamoDB clients."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("DYNAMO_DB_TABLE", "rekognitionAnalyzeDB-dev")
    monkeypatch.setenv("REKOGNITION_SNS_TOPIC_ARN", TOPIC_ARN)
    monkeypatch.setenv("REKOGNITION_ROLE_ARN", ROLE_ARN)
    spec = importlib.util.spec_from_file_location("rekognition_handler", HANDLER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.rekognition = MagicMock()
    module.rekognition.detect_labels.return_value = json.loads(json.dumps(IMAGE_LABELS))
    module.rekognition.start_label_detection.return_value = {"JobId": "job-1"}
    module.dynamodb = MagicMock()
    return module


def s3_event(key, bucket="rekognition-media-123456789012-dev"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def sns_event(status="SUCCEEDED", key="clips/my video.mp4", bucket="media-bucket"):
    message = {
        "JobId": "job-1",
        "Status": status,
        "API": "StartLabelDetection",
        "Video": {"S3ObjectName": key, "S3Bucket": bucket},
    }
    return {"Records": [{"Sns": {"Message": json.dumps(message)}}]}


def video_label(name, confidence, timestamp):
    return {"Timestamp": timestamp, "Label": {"Name": name, "Confidence": confidence, "Instances": []}}


def saved_items(handler):
    return [call.kwargs["Item"] for call in handler.dynamodb.Table.return_value.put_item.call_args_list]


@pytest.mark.parametrize(
    ("key", "extension"),
    [("photo.jpg", "jpg"), ("a/b/Cat.JPEG", "jpeg"), ("clip.MP4", "mp4"), ("notes", ""), ("dir.v2/notes", "")],
)
def test_media_extension(handler, key, extension):
    assert handler.media_extension(key) == extension


def test_image_labels_are_saved_with_the_decoded_key(handler):
    # S3 sends "holiday/my photo.png" as "holiday/my+photo.png".
    handler.start_processing_media(s3_event("holiday/my+photo.png"), None)

    handler.rekognition.detect_labels.assert_called_once_with(
        Image={"S3Object": {"Bucket": "rekognition-media-123456789012-dev", "Name": "holiday/my photo.png"}},
        MaxLabels=10,
    )
    handler.dynamodb.Table.assert_called_with("rekognitionAnalyzeDB-dev")
    [item] = saved_items(handler)
    assert item["mediaType"] == "Image"
    assert item["mediaName"] == "holiday/my photo.png"
    assert item["mediaBucket"] == "rekognition-media-123456789012-dev"
    assert item["Labels"][0]["Confidence"] == Decimal("99.5")
    assert item["Labels"][0]["Instances"][0]["BoundingBox"]["Width"] == Decimal("0.5")
    assert "ResponseMetadata" not in item
    assert item["id"] and item["createdAt"]


def test_upper_case_jpg_is_processed(handler):
    handler.start_processing_media(s3_event("CAMERA.JPG"), None)

    handler.rekognition.detect_labels.assert_called_once()


def test_video_starts_a_rekognition_job_that_reports_to_sns(handler):
    handler.start_processing_media(s3_event("clips/my+video.mp4", bucket="media-bucket"), None)

    handler.rekognition.start_label_detection.assert_called_once_with(
        Video={"S3Object": {"Bucket": "media-bucket", "Name": "clips/my video.mp4"}},
        NotificationChannel={"SNSTopicArn": TOPIC_ARN, "RoleArn": ROLE_ARN},
    )
    handler.rekognition.detect_labels.assert_not_called()
    assert saved_items(handler) == []


@pytest.mark.parametrize("key", ["notes.txt", "photo.gif", "README"])
def test_other_files_are_skipped(handler, key):
    handler.start_processing_media(s3_event(key), None)

    handler.rekognition.detect_labels.assert_not_called()
    handler.rekognition.start_label_detection.assert_not_called()
    assert saved_items(handler) == []


def test_finished_video_reads_every_page_and_saves_a_summary(handler):
    handler.rekognition.get_label_detection.side_effect = [
        {
            "JobStatus": "SUCCEEDED",
            "VideoMetadata": {"FrameRate": 29.97, "DurationMillis": 5000},
            "Labels": [video_label("Dog", 90.0, 0), video_label("Grass", 80.5, 0)],
            "NextToken": "page-2",
            "ResponseMetadata": {},
        },
        {"Labels": [video_label("Dog", 95.5, 1000), video_label("Dog", 70.0, 2000)]},
    ]

    handler.handle_label_detection(sns_event(), None)

    calls = handler.rekognition.get_label_detection.call_args_list
    assert calls[0].kwargs == {"JobId": "job-1", "SortBy": "TIMESTAMP"}
    assert calls[1].kwargs == {"JobId": "job-1", "SortBy": "TIMESTAMP", "NextToken": "page-2"}

    [item] = saved_items(handler)
    assert item["mediaType"] == "Video"
    assert item["mediaName"] == "clips/my video.mp4"
    assert item["mediaBucket"] == "media-bucket"
    assert "JobStatus" not in item and "NextToken" not in item
    assert item["VideoMetadata"]["FrameRate"] == Decimal("29.97")
    assert item["Labels"] == [
        {"Name": "Dog", "Confidence": Decimal("95.5"), "Occurrences": 3, "FirstSeenMs": 0, "LastSeenMs": 2000},
        {"Name": "Grass", "Confidence": Decimal("80.5"), "Occurrences": 1, "FirstSeenMs": 0, "LastSeenMs": 0},
    ]


def test_failed_video_job_is_not_saved(handler):
    handler.handle_label_detection(sns_event(status="FAILED"), None)

    handler.rekognition.get_label_detection.assert_not_called()
    assert saved_items(handler) == []


def test_make_item_turns_every_float_into_decimal(handler):
    data = {"a": 1.5, "b": [0.25, {"c": 2.0}], "d": "text", "e": 3, "f": True}

    assert handler.make_item(data) == {
        "a": Decimal("1.5"),
        "b": [Decimal("0.25"), {"c": Decimal("2.0")}],
        "d": "text",
        "e": 3,
        "f": True,
    }


def test_summary_is_sorted_by_confidence_then_name(handler):
    labels = [video_label("B", 50.0, 10), video_label("A", 50.0, 20), video_label("C", 99.0, 5)]

    summary = handler.summarize_video_labels(labels)

    assert [label["Name"] for label in summary] == ["C", "A", "B"]
    assert handler.summarize_video_labels([]) == []
