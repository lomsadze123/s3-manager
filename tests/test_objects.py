from unittest.mock import MagicMock, patch

import pytest

from s3_manager.objects import download_file_and_upload_to_s3, set_object_access_policy, upload_file


def test_upload_file_uses_file_name_and_content_type(client, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"fake image")

    key = upload_file(client, photo, "my-bucket")

    assert key == "photo.jpg"
    client.upload_file.assert_called_once_with(
        str(photo), "my-bucket", "photo.jpg", ExtraArgs={"ContentType": "image/jpeg"}
    )


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
