import json
from unittest.mock import patch

import pytest

from s3_manager import quotes

# Shortened real answer of https://api.quotable.kurokeita.dev/api/quotes/random
QUOTE = {
    "id": "WAn2olKpNv",
    "content": "It always seems impossible until it's done.",
    "tags": [{"id": "unDK8Rkf6F", "name": "Famous Quotes"}],
    "author": {"id": "c51OhtxdBi", "name": "Nelson Mandela", "slug": "nelson-mandela"},
}


def fake_api(mock_get, body):
    mock_get.return_value.json.return_value = body


@patch("s3_manager.quotes.requests.get")
def test_get_quote_random_sends_no_author(mock_get):
    fake_api(mock_get, {"quote": QUOTE})

    assert quotes.get_quote() == QUOTE
    assert mock_get.call_args.args == (quotes.QUOTES_API_URL,)
    assert mock_get.call_args.kwargs["params"] is None
    assert mock_get.call_args.kwargs["headers"] == quotes.HEADERS
    mock_get.return_value.raise_for_status.assert_called_once()


@patch("s3_manager.quotes.requests.get")
def test_get_quote_by_author(mock_get):
    fake_api(mock_get, {"quote": QUOTE})

    quotes.get_quote("Nelson Mandela")

    assert mock_get.call_args.kwargs["params"] == {"author": "Nelson Mandela"}


@patch("s3_manager.quotes.requests.get")
def test_get_quote_unknown_author(mock_get):
    # The API answers 200 OK with {} for an author it doesn't know (e.g. Linus Torvalds).
    fake_api(mock_get, {})

    with pytest.raises(ValueError, match="No quotes found for author 'Linus Torvalds'"):
        quotes.get_quote("Linus Torvalds")


@patch("s3_manager.quotes.requests.get")
def test_get_quote_empty_random_answer(mock_get):
    fake_api(mock_get, {})

    with pytest.raises(ValueError, match="returned no quote"):
        quotes.get_quote()


def test_format_quote_matches_lecture_output():
    assert quotes.format_quote(QUOTE) == "It always seems impossible until it's done.\n---\nNelson Mandela"


def test_quote_key_uses_author_slug_and_id():
    assert quotes.quote_key(QUOTE) == "quotes/nelson-mandela/WAn2olKpNv.json"


def test_save_quote_puts_json_object(client):
    key = quotes.save_quote(client, "my-bucket", QUOTE)

    assert key == "quotes/nelson-mandela/WAn2olKpNv.json"
    kwargs = client.put_object.call_args.kwargs
    assert (kwargs["Bucket"], kwargs["Key"], kwargs["ContentType"]) == ("my-bucket", key, "application/json")
    assert json.loads(kwargs["Body"]) == QUOTE


@pytest.mark.parametrize(
    ("key", "expected"),
    [("today.json", "today.json"), ("daily/today", "daily/today.json")],
)
def test_save_quote_custom_key_always_ends_with_json(client, key, expected):
    assert quotes.save_quote(client, "my-bucket", QUOTE, key=key) == expected
    assert client.put_object.call_args.kwargs["Key"] == expected
