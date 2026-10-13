from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from s3_manager import objects
from s3_manager.objects import (
    delete_object,
    download_file_and_upload_to_s3,
    get_object_info,
    set_object_access_policy,
    upload_file,
    upload_large_file,
)
from tests.conftest import make_client_error


# The first bytes ("magic numbers") of real files, enough for python-magic to detect the type.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
HTML_BYTES = b"<!doctype html><html><body>Not an image</body></html>"


def fake_download(mock_get, content, content_type="application/octet-stream"):
    mock_get.return_value = MagicMock(content=content, headers={"Content-Type": content_type})


@pytest.fixture
def small_parts(monkeypatch):
    """Allow tiny multipart parts so the tests don't need files of many megabytes."""
    monkeypatch.setattr(objects, "MIN_PART_SIZE", 1)


def test_upload_file_small_uses_put_object(client, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"fake image")

    key, method = upload_file(client, photo, "my-bucket")

    assert (key, method) == ("photo.jpg", "small")
    client.put_object.assert_called_once_with(
        Bucket="my-bucket", Key="photo.jpg", Body=b"fake image", ContentType="image/jpeg"
    )
    client.create_multipart_upload.assert_not_called()


def test_upload_file_auto_uses_multipart_from_threshold(client, tmp_path, small_parts):
    data = tmp_path / "data.bin"
    data.write_bytes(b"x" * 10)
    client.upload_part.return_value = {"ETag": '"etag"'}

    key, method = upload_file(client, data, "my-bucket", key="big/data.bin", threshold=10, part_size=4)

    assert (key, method) == ("big/data.bin", "large")
    client.put_object.assert_not_called()
    client.complete_multipart_upload.assert_called_once()


def test_upload_large_file_sends_parts_in_order(client, tmp_path, small_parts):
    data = tmp_path / "data.bin"
    data.write_bytes(b"aaaabbbbcc")
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.side_effect = [{"ETag": '"e1"'}, {"ETag": '"e2"'}, {"ETag": '"e3"'}]

    upload_large_file(client, data, "my-bucket", "data.bin", "application/octet-stream", part_size=4)

    client.create_multipart_upload.assert_called_once_with(
        Bucket="my-bucket", Key="data.bin", ContentType="application/octet-stream"
    )
    bodies = [call.kwargs["Body"] for call in client.upload_part.call_args_list]
    assert bodies == [b"aaaa", b"bbbb", b"cc"]
    client.complete_multipart_upload.assert_called_once_with(
        Bucket="my-bucket",
        Key="data.bin",
        UploadId="upload-1",
        MultipartUpload={
            "Parts": [
                {"ETag": '"e1"', "PartNumber": 1},
                {"ETag": '"e2"', "PartNumber": 2},
                {"ETag": '"e3"', "PartNumber": 3},
            ]
        },
    )
    client.abort_multipart_upload.assert_not_called()


def test_upload_large_file_aborts_when_a_part_fails(client, tmp_path, small_parts):
    data = tmp_path / "data.bin"
    data.write_bytes(b"aaaabbbb")
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.side_effect = [{"ETag": '"e1"'}, ConnectionError("network down")]

    with pytest.raises(ConnectionError):
        upload_large_file(client, data, "my-bucket", "data.bin", part_size=4)

    client.abort_multipart_upload.assert_called_once_with(
        Bucket="my-bucket", Key="data.bin", UploadId="upload-1"
    )
    client.complete_multipart_upload.assert_not_called()


def test_upload_large_file_rejects_parts_below_5_mib(client, tmp_path):
    data = tmp_path / "data.bin"
    data.write_bytes(b"x")

    with pytest.raises(ValueError, match="5 MiB"):
        upload_large_file(client, data, "my-bucket", "data.bin", part_size=1024)

    client.create_multipart_upload.assert_not_called()


def test_upload_file_rejects_unknown_method(client, tmp_path):
    data = tmp_path / "data.bin"
    data.write_bytes(b"x")

    with pytest.raises(ValueError, match="Upload method"):
        upload_file(client, data, "my-bucket", method="medium")


def test_upload_file_validate_mime_uses_detected_type(client, tmp_path):
    # No extension in the file name: the type comes from the content and .png is added to the key.
    image = tmp_path / "logo"
    image.write_bytes(PNG_BYTES)

    key, _ = upload_file(client, image, "my-bucket", validate_mime=True)

    assert key == "logo.png"
    assert client.put_object.call_args.kwargs["ContentType"] == "image/png"


@pytest.mark.parametrize(
    ("file_name", "content", "error"),
    [
        ("page.jpg", HTML_BYTES, "Unsupported file type 'text/html'"),
        ("photo.png", JPEG_BYTES, "image/jpeg"),
    ],
)
def test_upload_file_validate_mime_rejects_bad_files(client, tmp_path, file_name, content, error):
    path = tmp_path / file_name
    path.write_bytes(content)

    with pytest.raises(ValueError, match=error):
        upload_file(client, path, "my-bucket", validate_mime=True)

    client.put_object.assert_not_called()
    client.create_multipart_upload.assert_not_called()


@patch("s3_manager.objects.requests.get")
def test_download_file_and_upload_to_s3(mock_get, client):
    fake_download(mock_get, PNG_BYTES)

    key = download_file_and_upload_to_s3(client, "https://example.com/img/logo.png?v=2", "my-bucket")

    assert key == "logo.png"
    file_obj, bucket, uploaded_key = client.upload_fileobj.call_args.args
    assert file_obj.read() == PNG_BYTES
    assert (bucket, uploaded_key) == ("my-bucket", "logo.png")
    # Content-Type comes from the detected type, not from the server's (wrong) header.
    assert client.upload_fileobj.call_args.kwargs == {"ExtraArgs": {"ContentType": "image/png"}}


@pytest.mark.parametrize(
    ("content", "url", "expected_key"),
    [
        (JPEG_BYTES, "https://example.com/photo.jpeg", "photo.jpeg"),
        (MP4_BYTES, "https://example.com/clip.MP4", "clip.MP4"),
        (JPEG_BYTES, "https://picsum.photos/400", "400.jpg"),  # no extension: added from the type
    ],
)
@patch("s3_manager.objects.requests.get")
def test_download_file_and_upload_to_s3_accepts_allowed_types(mock_get, client, content, url, expected_key):
    fake_download(mock_get, content)

    assert download_file_and_upload_to_s3(client, url, "my-bucket") == expected_key


@patch("s3_manager.objects.requests.get")
def test_download_file_and_upload_to_s3_rejects_unsupported_type(mock_get, client):
    # The server claims it is an image, but the content is HTML.
    fake_download(mock_get, HTML_BYTES, content_type="image/jpeg")

    with pytest.raises(ValueError, match="Unsupported file type 'text/html'"):
        download_file_and_upload_to_s3(client, "https://example.com/photo.jpg", "my-bucket")

    client.upload_fileobj.assert_not_called()


@patch("s3_manager.objects.requests.get")
def test_download_file_and_upload_to_s3_rejects_wrong_extension(mock_get, client):
    fake_download(mock_get, JPEG_BYTES)

    with pytest.raises(ValueError, match="image/jpeg"):
        download_file_and_upload_to_s3(client, "https://example.com/photo.png", "my-bucket")

    client.upload_fileobj.assert_not_called()


def test_set_object_access_policy_rejects_unknown_acl(client):
    with pytest.raises(ValueError):
        set_object_access_policy(client, "my-bucket", "photo.jpg", acl="public-read-write")

    client.put_object_acl.assert_not_called()


def test_get_object_info_returns_size_and_type(client):
    client.head_object.return_value = {"ContentLength": 10, "ContentType": "image/jpeg"}

    assert get_object_info(client, "my-bucket", "dev/photo.jpg") == {"size": 10, "content_type": "image/jpeg"}
    client.head_object.assert_called_once_with(Bucket="my-bucket", Key="dev/photo.jpg")


def test_get_object_info_returns_none_for_missing_object(client):
    client.head_object.side_effect = make_client_error("404")

    assert get_object_info(client, "my-bucket", "missing.jpg") is None


def test_get_object_info_raises_on_access_denied(client):
    client.head_object.side_effect = make_client_error("403")

    with pytest.raises(ClientError):
        get_object_info(client, "my-bucket", "photo.jpg")


def test_delete_object_deletes_the_key(client):
    delete_object(client, "my-bucket", "dev/photo.jpg")

    client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="dev/photo.jpg")
