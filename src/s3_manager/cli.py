"""Command-line interface for s3-manager.

This module only handles user interaction (arguments, prompts, output).
The actual S3 work is done in buckets.py, objects.py, policies.py and lifecycle.py.
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

from s3_manager import buckets, lifecycle, objects, policies
from s3_manager.client import init_client

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="A simple CLI tool for working with S3-compatible storage.",
    no_args_is_help=True,
)

bucket_app = typer.Typer(help="Manage buckets.", no_args_is_help=True)
object_app = typer.Typer(help="Upload objects and manage object access.", no_args_is_help=True)
policy_app = typer.Typer(help="Generate, apply and read bucket policies.", no_args_is_help=True)
lifecycle_app = typer.Typer(
    help="Delete objects automatically some days after they were created.", no_args_is_help=True
)

app.add_typer(bucket_app, name="bucket")
app.add_typer(object_app, name="object")
app.add_typer(policy_app, name="policy")
app.add_typer(lifecycle_app, name="lifecycle")

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
    "EntityTooSmall": "A multipart part is smaller than 5 MiB. Use a bigger --part-size-mb.",
    "NoSuchUpload": "The multipart upload no longer exists (it was aborted or completed).",
    "InvalidArgument": "AWS rejected one of the arguments. Run with --verbose for details.",
    "InvalidLocationConstraint": "Unknown region. Use a region name like eu-central-1.",
    "IllegalLocationConstraintException": "The region does not match the S3 endpoint. Check --region and S3_ENDPOINT_URL.",
    "OperationAborted": "Another request is changing this bucket right now, try again in a moment.",
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
    region: Annotated[
        Optional[str],
        typer.Option(help="Region to create the bucket in, e.g. eu-central-1. Default: AWS_REGION from .env."),
    ] = None,
    versioning: Annotated[
        bool, typer.Option("-vers", "--versioning", help="Enable versioning right after creating the bucket.")
    ] = False,
):
    """Create a new bucket, optionally in another region and with versioning enabled."""
    client = init_client(region=region)
    buckets.create_bucket(client, name, enable_acl=enable_acl)
    typer.echo(f"Bucket '{name}' created in {client.meta.region_name}.")
    if versioning:
        buckets.set_bucket_versioning(client, name, enabled=True)
        typer.echo(f"Versioning of bucket '{name}' enabled.")


@bucket_app.command("delete")
def bucket_delete(
    name: Annotated[str, typer.Argument(help="Name of the bucket to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Delete an (empty) bucket if it exists."""
    client = init_client()
    if not buckets.bucket_exists(client, name):
        typer.echo(f"Bucket '{name}' does not exist. Nothing to delete.")
        return
    if not yes:
        typer.confirm(f"Really delete bucket '{name}'?", abort=True)
    buckets.delete_bucket(client, name)
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


@bucket_app.command("ensure")
def bucket_ensure(
    name: Annotated[str, typer.Argument(help="Name of the bucket to check or create.")],
    enable_acl: Annotated[
        bool, typer.Option("--enable-acl", help="Allow object ACLs if the bucket gets created.")
    ] = False,
):
    """Create a bucket only if it does not exist yet."""
    if buckets.ensure_bucket(init_client(), name, enable_acl=enable_acl):
        typer.echo(f"Bucket '{name}' did not exist, so it was created.")
    else:
        typer.echo(f"Bucket '{name}' already exists.")


VERSIONING_MESSAGES = {
    "Enabled": "Versioning is enabled on bucket '{name}'.",
    "Suspended": "Versioning is suspended on bucket '{name}' (it was enabled before, old versions are kept).",
    None: "Versioning has never been enabled on bucket '{name}'.",
}


@bucket_app.command("versioning")
def bucket_versioning(
    name: Annotated[str, typer.Argument(help="Name of the bucket.")],
    status: Annotated[
        bool, typer.Option("-vs", "--status", help="Show whether versioning is enabled.")
    ] = False,
    enable: Annotated[bool, typer.Option("--enable", help="Turn versioning on.")] = False,
    suspend: Annotated[bool, typer.Option("--suspend", help="Suspend versioning.")] = False,
):
    """Show the versioning status of a bucket with -vs, or change it with --enable / --suspend."""
    if enable and suspend:
        typer.echo("Use either --enable or --suspend, not both.")
        raise typer.Exit(code=1)
    if not (status or enable or suspend):
        typer.echo("Pass -vs to show the versioning status, or --enable / --suspend to change it.")
        raise typer.Exit(code=1)

    client = init_client()
    if not buckets.bucket_exists(client, name):
        typer.echo(f"Bucket '{name}' does not exist.")
        raise typer.Exit(code=1)

    if enable or suspend:
        buckets.set_bucket_versioning(client, name, enabled=enable)
        typer.echo(f"Versioning of bucket '{name}' {'enabled' if enable else 'suspended'}.")
    if status:
        typer.echo(VERSIONING_MESSAGES[buckets.get_bucket_versioning(client, name)].format(name=name))


@bucket_app.command("encryption")
def bucket_encryption(
    name: Annotated[str, typer.Argument(help="Name of the bucket.")],
    status: Annotated[
        bool, typer.Option("-es", "--status", help="Show the default encryption of the bucket.")
    ] = False,
    enable: Annotated[
        bool, typer.Option("--enable", help="Encrypt new objects by default with SSE-S3 (AES256).")
    ] = False,
):
    """Show the default encryption of a bucket with -es, or set it to AES256 with --enable."""
    if not (status or enable):
        typer.echo("Pass -es to show the default encryption, or --enable to set it to AES256.")
        raise typer.Exit(code=1)

    client = init_client()
    if not buckets.bucket_exists(client, name):
        typer.echo(f"Bucket '{name}' does not exist.")
        raise typer.Exit(code=1)

    if enable:
        buckets.set_bucket_encryption(client, name)
        typer.echo(f"Default encryption of bucket '{name}' set to AES256.")
    if status:
        algorithm = buckets.read_bucket_encryption(client, name)
        if algorithm is None:
            typer.echo(f"Bucket '{name}' has no default encryption.")
        else:
            typer.echo(f"Bucket '{name}' encrypts new objects with {algorithm}.")


@bucket_app.command("organize")
def bucket_organize(
    name: Annotated[str, typer.Argument(help="Name of the bucket.")],
    by_extension: Annotated[
        bool,
        typer.Option(
            "-ext", "--by-extension", help="Move each file into a folder named after its extension."
        ),
    ] = False,
    unknown: Annotated[
        bool,
        typer.Option("--unknown", help=f"Move files without an extension to {objects.UNKNOWN_FOLDER}/ instead of skipping them."),
    ] = False,
):
    """Move the files in the bucket root into folders by extension with -ext
    (image.jpg -> jpg/image.jpg) and show how many files were moved per extension."""
    if not by_extension:
        typer.echo("Pass -ext to move the files into folders named after their extension.")
        raise typer.Exit(code=1)

    client = init_client()
    if not buckets.bucket_exists(client, name):
        typer.echo(f"Bucket '{name}' does not exist.")
        raise typer.Exit(code=1)

    counts = objects.organize_by_extension(client, name, include_unknown=unknown)
    if not counts:
        what = "no files" if unknown else "no files with an extension"
        typer.echo(f"Nothing to organize: {what} in the bucket root.")
        return
    for extension, count in sorted(counts.items()):
        typer.echo(f"{extension} - {count}")


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
    method: Annotated[
        str,
        typer.Option(
            help="'small' = one put_object request, 'large' = own multipart upload, 'auto' = choose by "
            "file size, 'upload_file' / 'upload_fileobj' = boto3 managed transfer."
        ),
    ] = "auto",
    threshold_mb: Annotated[
        int, typer.Option(help="With --method auto, files of at least this size use multipart.")
    ] = objects.DEFAULT_MULTIPART_THRESHOLD // objects.MIB,
    part_size_mb: Annotated[
        int, typer.Option(help="Size of each multipart part (minimum 5).")
    ] = objects.DEFAULT_PART_SIZE // objects.MIB,
    validate_mime: Annotated[
        bool,
        typer.Option(
            "--validate-mime",
            help="Only allow .bmp, .jpg, .jpeg, .png, .webp and .mp4, detected from the file content.",
        ),
    ] = False,
    by_type: Annotated[
        bool,
        typer.Option(
            "-bt",
            "--by-type",
            help="Put the file into a folder named after its real type, detected from the content "
            f"with python-magic (fake.jpg that is a PNG -> png/fake.jpg, unknown types -> {objects.UNKNOWN_FOLDER}/).",
        ),
    ] = False,
):
    """Upload a local file to a bucket (small files in one request, large files in parts)."""
    key, used_method = objects.upload_file(
        init_client(),
        path,
        bucket,
        key=key,
        method=method,
        threshold=threshold_mb * objects.MIB,
        part_size=part_size_mb * objects.MIB,
        validate_mime=validate_mime,
        by_type=by_type,
    )
    typer.echo(f"Uploaded to s3://{bucket}/{key} ({used_method} upload)")


@object_app.command("list")
def object_list(
    bucket: Annotated[str, typer.Argument(help="Bucket to list.")],
    prefix: Annotated[str, typer.Option(help="Only list objects under this folder, e.g. dev/.")] = "",
):
    """List the objects in a bucket with their size and last change date."""
    client = init_client()
    if not buckets.bucket_exists(client, bucket):
        typer.echo(f"Bucket '{bucket}' does not exist.")
        raise typer.Exit(code=1)

    items = objects.get_objects(client, bucket, prefix=prefix)
    if not items:
        typer.echo(f"No objects in s3://{bucket}/{prefix}.")
        return
    for item in items:
        typer.echo(f"{item['key']}  {item['size']} bytes  {format_date(item['modified'])}")
    typer.echo(f"{len(items)} object(s), {sum(item['size'] for item in items)} bytes in total.")


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


@object_app.command("file")
def object_file(
    bucket: Annotated[str, typer.Argument(help="Bucket containing the object.")],
    key: Annotated[str, typer.Argument(help="Object key (file name in S3), e.g. dev/photo.jpg.")],
    delete: Annotated[bool, typer.Option("-del", "--delete", help="Delete the object.")] = False,
    versions: Annotated[
        bool, typer.Option("-ver", "--versions", help="Show the number of versions and their creation dates.")
    ] = False,
    restore_previous: Annotated[
        bool,
        typer.Option(
            "-rp", "--restore-previous", help="Upload the second-to-last version again as a new version."
        ),
    ] = False,
    rollback_to: Annotated[
        Optional[str],
        typer.Option(
            "-rbt", "--rollback-to", help="Upload the version with this id again as a new version (ids: -ver)."
        ),
    ] = None,
):
    """Show the size and type of an object, list its versions with -ver, upload its previous
    version again with -rp (or any version with -rbt VERSION_ID), or delete it with -del."""
    if delete + versions + restore_previous + bool(rollback_to) > 1:
        typer.echo("Use only one of -del, -ver, -rp and -rbt at a time.")
        raise typer.Exit(code=1)

    client = init_client()
    if not buckets.bucket_exists(client, bucket):
        typer.echo(f"Bucket '{bucket}' does not exist.")
        raise typer.Exit(code=1)

    if versions:
        show_object_versions(client, bucket, key)
        return
    if restore_previous or rollback_to:
        if rollback_to:
            source, new_version_id = objects.rollback_to_version(client, bucket, key, rollback_to)
        else:
            source, new_version_id = objects.restore_previous_version(client, bucket, key)
        typer.echo(
            f"Version {source['version_id']} (created {format_date(source['created'])}) "
            f"uploaded again as new version {new_version_id}."
        )
        return

    info = objects.get_object_info(client, bucket, key)
    if info is None:
        if delete:
            typer.echo(f"Object 's3://{bucket}/{key}' does not exist. Nothing to delete.")
            return
        typer.echo(f"Object 's3://{bucket}/{key}' does not exist.")
        raise typer.Exit(code=1)

    if delete:
        objects.delete_object(client, bucket, key)
        typer.echo(f"Object 's3://{bucket}/{key}' deleted.")
    else:
        typer.echo(f"s3://{bucket}/{key} exists ({info['size']} bytes, {info['content_type']}).")
        typer.echo("Pass -del to delete it.")


def format_date(date):
    """Format an S3 timestamp (a timezone-aware datetime in UTC) for the output."""
    return date.strftime("%Y-%m-%d %H:%M:%S UTC")


def show_object_versions(client, bucket, key):
    versions = objects.list_object_versions(client, bucket, key)
    if not versions:
        typer.echo(f"Object 's3://{bucket}/{key}' does not exist.")
        raise typer.Exit(code=1)

    typer.echo(f"s3://{bucket}/{key} has {len(versions)} version(s):")
    for number, version in enumerate(versions, start=1):
        latest = "  (latest)" if version["is_latest"] else ""
        typer.echo(
            f"  {number}. created {format_date(version['created'])}, {version['size']} bytes, "
            f"id {version['version_id']}{latest}"
        )


# ---------- policy commands ----------

@policy_app.command("generate-public-read")
def policy_generate(
    bucket: Annotated[str, typer.Argument(help="Bucket the policy is for.")],
    prefix: Annotated[
        Optional[list[str]],
        typer.Option(help="Only make this folder public (repeatable). Default: whole bucket."),
    ] = None,
):
    """Print a public-read bucket policy as JSON (does not apply it)."""
    policy = policies.generate_public_read_policy(bucket, prefix)
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


@policy_app.command("ensure")
def policy_ensure(
    bucket: Annotated[str, typer.Argument(help="Bucket to check.")],
    prefix: Annotated[
        list[str], typer.Option(help="Folder to make public (repeatable).")
    ] = ["dev", "test"],
):
    """If the bucket has no policy, make objects under the given folders publicly readable."""
    if policies.ensure_bucket_policy(init_client(), bucket, prefix):
        folders = ", ".join(f"{p.strip('/')}/" for p in prefix)
        typer.echo(f"Bucket '{bucket}' had no policy. Applied public-read policy for: {folders}")
    else:
        typer.echo(f"Bucket '{bucket}' already has a policy. Nothing changed.")


@policy_app.command("create-multiple")
def policy_create_multiple(
    bucket: Annotated[str, typer.Argument(help="Bucket to apply the policy to.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Turn off Block Public Access and let everyone list versions, read objects, change object
    ACLs and DELETE objects (the lecture's multiple_policy). Only for test buckets."""
    if not yes:
        typer.confirm(
            f"Anyone on the internet will be able to read AND delete the files in '{bucket}'. Continue?",
            abort=True,
        )
    client = init_client()
    policies.disable_block_public_access(client, bucket)
    policies.create_bucket_policy(client, bucket, policies.generate_multiple_policy(bucket))
    typer.echo(f"Multiple-permissions policy applied to '{bucket}'.")


@policy_app.command("set-public")
def policy_set_public(
    bucket: Annotated[str, typer.Argument(help="Bucket to make public.")],
):
    """Turn off Block Public Access and apply a public-read policy."""
    client = init_client()
    policies.disable_block_public_access(client, bucket)
    policies.create_bucket_policy(client, bucket)
    typer.echo(f"Bucket '{bucket}' is now publicly readable.")


# ---------- lifecycle commands ----------

@lifecycle_app.command("set")
def lifecycle_set(
    bucket: Annotated[str, typer.Argument(help="Bucket to apply the lifecycle policy to.")],
    days: Annotated[
        int, typer.Option(help="Delete objects this many days after they were created.")
    ] = lifecycle.DEFAULT_EXPIRATION_DAYS,
    prefix: Annotated[
        str, typer.Option(help="Only delete objects under this folder. Default: whole bucket.")
    ] = "",
):
    """Delete objects automatically N days (default 120) after creation. Replaces existing rules."""
    rule = lifecycle.set_lifecycle_policy(init_client(), bucket, days=days, prefix=prefix)
    where = f"under '{rule['Filter']['Prefix']}'" if rule["Filter"]["Prefix"] else "in the bucket"
    typer.echo(f"Lifecycle policy set on '{bucket}': objects {where} are deleted {days} days after creation.")


@lifecycle_app.command("get")
def lifecycle_get(
    bucket: Annotated[str, typer.Argument(help="Bucket to read the lifecycle policy from.")],
):
    """Show the current lifecycle rules."""
    rules = lifecycle.read_lifecycle_policy(init_client(), bucket)
    if rules is None:
        typer.echo(f"Bucket '{bucket}' has no lifecycle policy.")
    else:
        typer.echo(json.dumps(rules, indent=2))


@lifecycle_app.command("delete")
def lifecycle_delete(
    bucket: Annotated[str, typer.Argument(help="Bucket to remove the lifecycle policy from.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Remove all lifecycle rules from a bucket."""
    if not yes:
        typer.confirm(f"Really delete all lifecycle rules of '{bucket}'?", abort=True)
    lifecycle.delete_lifecycle_policy(init_client(), bucket)
    typer.echo(f"Lifecycle policy of '{bucket}' deleted.")
