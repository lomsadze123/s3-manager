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


@patch("s3_manager.objects.requests.get")
def test_download_file_and_upload_to_s3(mock_get, client):
    mock_get.return_value = MagicMock(content=b"png bytes", headers={"Content-Type": "image/png"})

    key = download_file_and_upload_to_s3(client, "https://example.com/img/logo.png?v=2", "my-bucket")

    assert key == "logo.png"
    file_obj, bucket, uploaded_key = client.upload_fileobj.call_args.args
    assert file_obj.read() == b"png bytes"
    assert (bucket, uploaded_key) == ("my-bucket", "logo.png")
    assert client.upload_fileobj.call_args.kwargs == {"ExtraArgs": {"ContentType": "image/png"}}


def test_set_object_access_policy_rejects_unknown_acl(client):
    with pytest.raises(ValueError):
        set_object_access_policy(client, "my-bucket", "photo.jpg", acl="public-read-write")

    client.put_object_acl.assert_not_called()
