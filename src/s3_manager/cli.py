"""Command-line interface for s3-manager.

This module only handles user interaction (arguments, prompts, output).
The actual S3 work is done in buckets.py, objects.py and policies.py.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Annotated, Optional

import requests
import typer
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ParamValidationError

from s3_manager import buckets, objects, policies
from s3_manager.client import init_client

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="A simple CLI tool for working with S3-compatible storage.",
    no_args_is_help=True,
)

bucket_app = typer.Typer(help="Manage buckets.", no_args_is_help=True)
object_app = typer.Typer(help="Upload objects and manage object access.", no_args_is_help=True)
policy_app = typer.Typer(help="Generate, apply and read bucket policies.", no_args_is_help=True)

app.add_typer(bucket_app, name="bucket")
app.add_typer(object_app, name="object")
app.add_typer(policy_app, name="policy")

# Friendlier messages for common AWS error codes.
AWS_ERROR_MESSAGES = {
    "NoSuchBucket": "The bucket does not exist.",
    "NoSuchKey": "The object does not exist.",
    "BucketAlreadyExists": "This bucket name is already taken by another AWS account.",
    "BucketAlreadyOwnedByYou": "You already own a bucket with this name.",
    "BucketNotEmpty": "The bucket is not empty. Delete its objects first.",
    "InvalidBucketName": "Invalid bucket name. Use 3-63 lowercase letters, digits and hyphens.",
    "AccessDenied": "Access denied. Check your permissions and the bucket's Block Public Access settings.",
    "403": "Access denied. The bucket may belong to another AWS account.",
    "InvalidAccessKeyId": "The AWS access key ID is invalid. Check your .env file.",
    "SignatureDoesNotMatch": "The AWS secret access key is wrong. Check your .env file.",
    "AccessControlListNotSupported": "This bucket does not allow ACLs. Create it with --enable-acl.",
    "MalformedPolicy": "The bucket policy is not valid.",
}


@app.callback()
def configure(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug logs.")] = False,
):
    """A simple CLI tool for working with S3-compatible storage."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    logging.getLogger("s3_manager").setLevel(logging.DEBUG if verbose else logging.INFO)


def describe_aws_error(error):
    code = error.response["Error"].get("Code", "Unknown")
    message = AWS_ERROR_MESSAGES.get(code) or error.response["Error"].get("Message") or "AWS request failed."
    return f"{message} ({code})"


def fail(message):
    logger.error(message)
    logger.debug("Details:", exc_info=True)
    sys.exit(1)


def main():
    """Entry point: run the CLI and turn known errors into short messages."""
    try:
        app()
    except ClientError as error:
        fail(describe_aws_error(error))
    except NoCredentialsError:
        fail("AWS credentials not found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env.")
    except ParamValidationError:
        fail("Invalid input (check the bucket name). Run with --verbose for details.")
    except (BotoCoreError, S3UploadFailedError) as error:
        fail(str(error))
    except requests.HTTPError as error:
        fail(f"Download failed: {error}")
    except requests.RequestException as error:
        fail(f"Download failed: could not reach the URL ({type(error).__name__}).")
    except ValueError as error:
        fail(str(error))


# ---------- bucket commands ----------

@bucket_app.command("list")
def bucket_list():
    """List all buckets."""
    names = buckets.list_buckets(init_client())
    if not names:
        typer.echo("No buckets found.")
    for name in names:
        typer.echo(name)


@bucket_app.command("create")
def bucket_create(
    name: Annotated[str, typer.Argument(help="Name of the new bucket.")],
    enable_acl: Annotated[
        bool, typer.Option("--enable-acl", help="Allow object ACLs (needed for 'object set-acl').")
    ] = False,
):
    """Create a new bucket."""
    buckets.create_bucket(init_client(), name, enable_acl=enable_acl)
    typer.echo(f"Bucket '{name}' created.")


@bucket_app.command("delete")
def bucket_delete(
    name: Annotated[str, typer.Argument(help="Name of the bucket to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Delete an (empty) bucket."""
    if not yes:
        typer.confirm(f"Really delete bucket '{name}'?", abort=True)
    buckets.delete_bucket(init_client(), name)
    typer.echo(f"Bucket '{name}' deleted.")


@bucket_app.command("exists")
def bucket_exists_cmd(
    name: Annotated[str, typer.Argument(help="Name of the bucket to check.")],
):
    """Check whether a bucket exists."""
    if buckets.bucket_exists(init_client(), name):
        typer.echo(f"Bucket '{name}' exists.")
    else:
        typer.echo(f"Bucket '{name}' does not exist.")
        raise typer.Exit(code=1)


# ---------- object commands ----------

@object_app.command("upload")
def object_upload(
    path: Annotated[
        Path, typer.Argument(help="Local file to upload.", exists=True, dir_okay=False)
    ],
    bucket: Annotated[str, typer.Argument(help="Target bucket.")],
    key: Annotated[
        Optional[str], typer.Option(help="Object key in S3. Defaults to the file name.")
    ] = None,
):
    """Upload a local file to a bucket."""
    key = objects.upload_file(init_client(), path, bucket, key=key)
    typer.echo(f"Uploaded to s3://{bucket}/{key}")


@object_app.command("upload-url")
def object_upload_url(
    url: Annotated[str, typer.Argument(help="URL of the file to download.")],
    bucket: Annotated[str, typer.Argument(help="Target bucket.")],
    key: Annotated[
        Optional[str], typer.Option(help="Object key in S3. Defaults to the file name from the URL.")
    ] = None,
):
    """Download a file from a URL and upload it to a bucket."""
    key = objects.download_file_and_upload_to_s3(init_client(), url, bucket, key=key)
    typer.echo(f"Uploaded to s3://{bucket}/{key}")


@object_app.command("set-acl")
def object_set_acl(
    bucket: Annotated[str, typer.Argument(help="Bucket containing the object.")],
    key: Annotated[str, typer.Argument(help="Object key.")],
    acl: Annotated[str, typer.Option(help="ACL to apply: 'public-read' or 'private'.")] = "public-read",
):
    """Set the access ACL of a single object."""
    objects.set_object_access_policy(init_client(), bucket, key, acl=acl)
    typer.echo(f"ACL of s3://{bucket}/{key} set to '{acl}'.")


# ---------- policy commands ----------

@policy_app.command("generate-public-read")
def policy_generate(
    bucket: Annotated[str, typer.Argument(help="Bucket the policy is for.")],
):
    """Print a public-read bucket policy as JSON (does not apply it)."""
    policy = policies.generate_public_read_policy(bucket)
    typer.echo(json.dumps(policy, indent=2))


@policy_app.command("create")
def policy_create(
    bucket: Annotated[str, typer.Argument(help="Bucket to apply the policy to.")],
):
    """Apply a public-read bucket policy."""
    policies.create_bucket_policy(init_client(), bucket)
    typer.echo(f"Public-read policy applied to '{bucket}'.")


@policy_app.command("get")
def policy_get(
    bucket: Annotated[str, typer.Argument(help="Bucket to read the policy from.")],
):
    """Show the current bucket policy."""
    policy = policies.read_bucket_policy(init_client(), bucket)
    if policy is None:
        typer.echo(f"Bucket '{bucket}' has no policy.")
    else:
        typer.echo(json.dumps(policy, indent=2))


@policy_app.command("set-public")
def policy_set_public(
    bucket: Annotated[str, typer.Argument(help="Bucket to make public.")],
):
    """Turn off Block Public Access and apply a public-read policy."""
    client = init_client()
    policies.disable_block_public_access(client, bucket)
    policies.create_bucket_policy(client, bucket)
    typer.echo(f"Bucket '{bucket}' is now publicly readable.")
