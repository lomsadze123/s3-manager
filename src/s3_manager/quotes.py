"""Inspirational quotes from https://api.quotable.kurokeita.dev, optionally saved to a bucket as JSON.

Based on the lecture 5 "inspire" script. The API returns {"quote": {...}} with the text in
"content" and the author in "author" (name, slug, bio, ...). For an author it doesn't know,
it still answers 200 OK, but with an empty object {}.
"""

import json
import logging

import requests

logger = logging.getLogger(__name__)

QUOTES_API_URL = "https://api.quotable.kurokeita.dev/api/quotes/random"
HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
}
QUOTES_FOLDER = "quotes"


def get_quote(author=None):
    """Return a random quote (the dict inside "quote"), or a random quote by `author`.

    The author can be a full name ("Nelson Mandela") or a slug ("nelson-mandela").
    Raises ValueError if the API has no quote for the author.
    """
    params = {"author": author} if author else None
    logger.debug("Fetching a quote from %s (author: %s)...", QUOTES_API_URL, author or "any")
    response = requests.get(QUOTES_API_URL, params=params, headers=HEADERS, timeout=10)
    response.raise_for_status()

    quote = response.json().get("quote")
    if not quote:
        if author:
            raise ValueError(f"No quotes found for author '{author}'. Check the spelling or try another author.")
        raise ValueError("The quotes API returned no quote.")
    return quote


def format_quote(quote):
    """The text, a --- line and the author, like print_quote in the lecture script."""
    return f"{quote['content']}\n---\n{quote['author']['name']}"


def quote_key(quote):
    """Default object key: quotes/<author slug>/<quote id>.json"""
    return f"{QUOTES_FOLDER}/{quote['author']['slug']}/{quote['id']}.json"


def save_quote(client, bucket, quote, key=None):
    """Save the quote as a .json object in the bucket. Returns the object key.

    A custom key without .json at the end gets it added.
    """
    key = key or quote_key(quote)
    if not key.endswith(".json"):
        key += ".json"
    body = json.dumps(quote, indent=2, ensure_ascii=False).encode()

    logger.debug("Uploading quote to s3://%s/%s...", bucket, key)
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return key
