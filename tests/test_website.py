from unittest.mock import call

import pytest
from botocore.exceptions import ClientError

from s3_manager.website import (
    ASSET_CACHE_CONTROL,
    HTML_CACHE_CONTROL,
    cache_control,
    check_site_folder,
    collect_site_files,
    delete_website,
    find_error_document,
    host_static_website,
    read_bucket_website,
    set_bucket_website_policy,
    static_web_page_file,
    website_content_type,
    website_url,
)
from tests.conftest import make_client_error
from tests.test_objects import PNG_BYTES


@pytest.fixture
def react_dist(tmp_path):
    """A folder that looks like the dist/ folder of 'npm run build'."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>App</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('hi')")
    (dist / "assets" / "index-abc123.css").write_text("body { margin: 0 }")
    (dist / "favicon.svg").write_text("<svg></svg>")
    (dist / ".DS_Store").write_bytes(b"\x00\x00")
    return dist


def test_set_bucket_website_policy_sets_index_document(client):
    set_bucket_website_policy(client, "my-site")

    client.put_bucket_website.assert_called_once_with(
        Bucket="my-site", WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}}
    )


def test_set_bucket_website_policy_adds_error_document(client):
    set_bucket_website_policy(client, "my-site", error_document="index.html")

    configuration = client.put_bucket_website.call_args.kwargs["WebsiteConfiguration"]
    assert configuration["ErrorDocument"] == {"Key": "index.html"}


def test_set_bucket_website_policy_switch_off_deletes_configuration(client):
    set_bucket_website_policy(client, "my-site", switch=False)

    client.delete_bucket_website.assert_called_once_with(Bucket="my-site")
    client.put_bucket_website.assert_not_called()


def test_read_bucket_website_returns_documents(client):
    client.get_bucket_website.return_value = {
        "IndexDocument": {"Suffix": "index.html"},
        "ErrorDocument": {"Key": "404.html"},
    }

    assert read_bucket_website(client, "my-site") == {"index": "index.html", "error": "404.html"}


def test_read_bucket_website_returns_none_when_hosting_is_off(client):
    client.get_bucket_website.side_effect = make_client_error("NoSuchWebsiteConfiguration")

    assert read_bucket_website(client, "my-site") is None


def test_read_bucket_website_raises_other_errors(client):
    client.get_bucket_website.side_effect = make_client_error("AccessDenied")

    with pytest.raises(ClientError):
        read_bucket_website(client, "my-site")


@pytest.mark.parametrize(
    ("region", "url"),
    [
        ("us-east-1", "http://my-site.s3-website-us-east-1.amazonaws.com"),
        ("eu-west-1", "http://my-site.s3-website-eu-west-1.amazonaws.com"),
        ("eu-central-1", "http://my-site.s3-website.eu-central-1.amazonaws.com"),
        ("us-east-2", "http://my-site.s3-website.us-east-2.amazonaws.com"),
    ],
)
def test_website_url_uses_dash_or_dot_by_region(region, url):
    assert website_url("my-site", region) == url


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("index.html", "text/html; charset=utf-8"),
        ("style.css", "text/css; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"),
        ("icon.svg", "image/svg+xml; charset=utf-8"),
        ("data.json", "application/json; charset=utf-8"),
        ("font.woff2", "font/woff2"),
    ],
)
def test_website_content_type_comes_from_extension(tmp_path, name, content_type):
    file = tmp_path / name
    file.write_text("x")

    assert website_content_type(file) == content_type


def test_website_content_type_detects_unknown_extension_from_content(tmp_path):
    file = tmp_path / "logo.unknownext"
    file.write_bytes(PNG_BYTES)

    assert website_content_type(file) == "image/png"


def test_cache_control_never_caches_html():
    assert cache_control("index.html") == HTML_CACHE_CONTROL
    assert cache_control("docs/Page.HTML") == HTML_CACHE_CONTROL
    assert cache_control("assets/index-abc123.js") == ASSET_CACHE_CONTROL


def test_collect_site_files_keeps_subfolders_and_skips_hidden_files(react_dist):
    keys = [key for _, key in collect_site_files(react_dist)]

    assert keys == ["assets/index-abc123.css", "assets/index-abc123.js", "favicon.svg", "index.html"]


def test_collect_site_files_single_file_uses_its_name(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<h1>Hi</h1>")

    assert collect_site_files(page) == [(page, "index.html")]


def test_static_web_page_file_uploads_with_content_type_and_cache(client, react_dist):
    keys = static_web_page_file(client, "my-site", react_dist)

    assert keys == ["assets/index-abc123.css", "assets/index-abc123.js", "favicon.svg", "index.html"]
    assert client.upload_file.call_count == 4
    client.upload_file.assert_any_call(
        str(react_dist / "assets" / "index-abc123.js"),
        "my-site",
        "assets/index-abc123.js",
        ExtraArgs={"ContentType": "text/javascript; charset=utf-8", "CacheControl": ASSET_CACHE_CONTROL},
    )
    client.upload_file.assert_any_call(
        str(react_dist / "index.html"),
        "my-site",
        "index.html",
        ExtraArgs={"ContentType": "text/html; charset=utf-8", "CacheControl": HTML_CACHE_CONTROL},
    )


def test_static_web_page_file_with_prefix(client, tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<h1>Hi</h1>")

    assert static_web_page_file(client, "my-site", page, prefix="/docs/") == ["docs/index.html"]


def test_static_web_page_file_rejects_missing_bucket(client, tmp_path):
    client.head_bucket.side_effect = make_client_error("404")
    page = tmp_path / "index.html"
    page.write_text("<h1>Hi</h1>")

    with pytest.raises(ValueError, match="does not exist"):
        static_web_page_file(client, "missing", page)
    client.upload_file.assert_not_called()


def test_check_site_folder_rejects_file_not_named_index(tmp_path):
    page = tmp_path / "me.html"
    page.write_text("<h1>Hi</h1>")

    with pytest.raises(ValueError, match="needs an index.html"):
        check_site_folder(page)


def test_check_site_folder_hints_npm_build_for_react_source(tmp_path):
    (tmp_path / "package.json").write_text("{}")

    with pytest.raises(ValueError, match="npm run build"):
        check_site_folder(tmp_path)


def test_host_static_website_does_everything_in_order(client, react_dist):
    client.get_bucket_location.return_value = {"LocationConstraint": "eu-central-1"}

    keys, url = host_static_website(client, "my-site", react_dist, spa=True)

    assert len(keys) == 4
    assert url == "http://my-site.s3-website.eu-central-1.amazonaws.com"
    names = [name for name, _, _ in client.mock_calls]
    order = ["head_bucket", "put_public_access_block", "put_bucket_policy", "put_bucket_website", "upload_file"]
    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)
    configuration = client.put_bucket_website.call_args.kwargs["WebsiteConfiguration"]
    assert configuration["ErrorDocument"] == {"Key": "index.html"}


def test_host_static_website_creates_missing_bucket(client, tmp_path):
    client.head_bucket.side_effect = [make_client_error("404"), None]
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    page = tmp_path / "index.html"
    page.write_text("<h1>Hi</h1>")

    _, url = host_static_website(client, "new-site", page)

    client.create_bucket.assert_called_once()
    assert url == "http://new-site.s3-website-us-east-1.amazonaws.com"
    assert "ErrorDocument" not in client.put_bucket_website.call_args.kwargs["WebsiteConfiguration"]


def test_host_static_website_checks_folder_before_any_aws_call(client, tmp_path):
    (tmp_path / "package.json").write_text("{}")

    with pytest.raises(ValueError):
        host_static_website(client, "my-site", tmp_path)
    assert client.mock_calls == []


def test_host_static_website_makes_whole_bucket_public(client, tmp_path):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    page = tmp_path / "index.html"
    page.write_text("<h1>Hi</h1>")

    host_static_website(client, "my-site", page)

    assert client.put_public_access_block.call_args == call(
        Bucket="my-site",
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False,
            "IgnorePublicAcls": False,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False,
        },
    )
    assert '"Resource": "arn:aws:s3:::my-site/*"' in client.put_bucket_policy.call_args.kwargs["Policy"]


def test_find_error_document_in_folder_and_next_to_file(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Hi</h1>")
    (tmp_path / "error.html").write_text("<h1>404</h1>")

    assert find_error_document(tmp_path, "error.html") == tmp_path / "error.html"
    assert find_error_document(tmp_path / "index.html", "error.html") == tmp_path / "error.html"


def test_find_error_document_rejects_missing_page(tmp_path):
    with pytest.raises(ValueError, match="'404.html' not found"):
        find_error_document(tmp_path, "404.html")


def test_host_static_website_uploads_error_page_next_to_single_file(client, tmp_path):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    (tmp_path / "index.html").write_text("<h1>Hi</h1>")
    (tmp_path / "error.html").write_text("<h1>404</h1>")

    keys, _ = host_static_website(client, "my-site", tmp_path / "index.html", error_document="error.html")

    assert keys == ["index.html", "error.html"]
    configuration = client.put_bucket_website.call_args.kwargs["WebsiteConfiguration"]
    assert configuration["ErrorDocument"] == {"Key": "error.html"}


def test_host_static_website_error_page_in_subfolder_keeps_its_key(client, tmp_path):
    client.get_bucket_location.return_value = {"LocationConstraint": None}
    (tmp_path / "index.html").write_text("<h1>Hi</h1>")
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "404.html").write_text("<h1>404</h1>")

    keys, _ = host_static_website(client, "my-site", tmp_path / "index.html", error_document="pages/404.html")

    assert keys == ["index.html", "pages/404.html"]


def test_host_static_website_checks_error_page_before_any_aws_call(client, tmp_path):
    (tmp_path / "index.html").write_text("<h1>Hi</h1>")

    with pytest.raises(ValueError, match="not found"):
        host_static_website(client, "my-site", tmp_path, error_document="error.html")
    with pytest.raises(ValueError, match="either --spa or --error-document"):
        host_static_website(client, "my-site", tmp_path, spa=True, error_document="error.html")
    assert client.mock_calls == []


def test_delete_website_only_turns_hosting_off(client):
    assert delete_website(client, "my-site") == 0

    client.delete_bucket_website.assert_called_once_with(Bucket="my-site")
    client.delete_object.assert_not_called()


def test_delete_website_purge_deletes_every_object(client):
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "index.html", "Size": 1, "LastModified": None}]},
        {"Contents": [{"Key": "assets/app.js", "Size": 1, "LastModified": None}]},
    ]

    assert delete_website(client, "my-site", purge=True) == 2

    client.delete_object.assert_any_call(Bucket="my-site", Key="index.html")
    client.delete_object.assert_any_call(Bucket="my-site", Key="assets/app.js")


def test_delete_website_rejects_missing_bucket(client):
    client.head_bucket.side_effect = make_client_error("404")

    with pytest.raises(ValueError, match="does not exist"):
        delete_website(client, "missing")
    client.delete_bucket_website.assert_not_called()
