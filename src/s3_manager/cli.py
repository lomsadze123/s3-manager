"""Command-line interface for s3-manager.

This module only handles user interaction (arguments, prompts, output).
The actual AWS work is done in buckets.py, objects.py, policies.py, lifecycle.py, website.py,
detection.py and quotes.py.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import requests
import typer
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ParamValidationError
from typer.core import TyperCommand, TyperGroup

from s3_manager import buckets, detection, lifecycle, objects, policies, quotes, website
from s3_manager.client import init_client, read_setting

logger = logging.getLogger(__name__)


class DefaultDeployGroup(TyperGroup):
    """The host group: 'host BUCKET --source FOLDER' runs 'host deploy BUCKET --source FOLDER'.

    If the first word is not a subcommand, it is a bucket name, so "deploy" is put in front.
    A bucket named like a subcommand (e.g. "status") still needs 'host deploy status ...'.
    """

    def parse_args(self, ctx, args):
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = ["deploy", *args]
        return super().parse_args(ctx, args)


class OptionalInspireCommand(TyperCommand):
    """The quote command: a bare '--inspire' means "any author".

    Typer options always need a value, so '--inspire' as the last word or before another option
    gets an empty one: 'quote BUCKET --inspire --save' runs 'quote BUCKET --inspire "" --save'.
    """

    def parse_args(self, ctx, args):
        fixed = []
        for index, arg in enumerate(args):
            fixed.append(arg)
            next_arg = args[index + 1] if index + 1 < len(args) else None
            if arg == "--inspire" and (next_arg is None or next_arg.startswith("-")):
                fixed.append("")
        return super().parse_args(ctx, fixed)


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
host_app = typer.Typer(
    cls=DefaultDeployGroup,
    help="Host a static website (a plain HTML page or a built React app) on S3. "
    "Shortcut: 'host BUCKET --source FOLDER' is the same as 'host deploy BUCKET --source FOLDER'.",
    no_args_is_help=True,
)
detect_app = typer.Typer(
    help="Detect objects on images with an AWS Lambda function (API Ninjas or Hugging Face).",
    no_args_is_help=True,
)

app.add_typer(bucket_app, name="bucket")
app.add_typer(object_app, name="object")
app.add_typer(policy_app, name="policy")
app.add_typer(lifecycle_app, name="lifecycle")
app.add_typer(host_app, name="host")
app.add_typer(detect_app, name="detect")

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
    "NoSuchWebsiteConfiguration": "Static website hosting is not enabled on this bucket.",
    "ResourceNotFoundException": "The Lambda function does not exist.",
    "InvalidParameterValueException": "Lambda rejected the settings (often the role: check --role-arn / LabRole).",
    "AccessDeniedException": "Access denied by Lambda. Check that your credentials may use Lambda and pass the role.",
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
        fail(f"Request failed: {error}")
    except requests.RequestException as error:
        fail(f"Request failed: could not reach the URL ({type(error).__name__}).")
    except (ValueError, TimeoutError) as error:
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
    print_versions(bucket, key, versions)


def print_versions(bucket, key, versions, old_versions=(), old_label=""):
    """Print the versions of one object, newest first, marking the ones in old_versions."""
    typer.echo(f"s3://{bucket}/{key} has {len(versions)} version(s):")
    for number, version in enumerate(versions, start=1):
        latest = "  (latest)" if version["is_latest"] else ""
        old = f"  -> {old_label}" if version in old_versions else ""
        typer.echo(
            f"  {number}. created {format_date(version['created'])}, {version['size']} bytes, "
            f"id {version['version_id']}{latest}{old}"
        )


@object_app.command("old-versions")
def object_old_versions(
    bucket: Annotated[str, typer.Argument(help="Bucket containing the objects.")],
    keys: Annotated[list[str], typer.Argument(help="One or more object keys, e.g. notes/photo.jpg data.csv.")],
    months: Annotated[
        int,
        typer.Option(help="Delete versions created more than this many months ago (0 = every older version)."),
    ] = objects.DEFAULT_VERSION_MAX_AGE_MONTHS,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only show which versions would be deleted.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Show the versions of each file, then permanently delete the versions created more than
    6 months ago (--months). The latest version of a file is always kept."""
    if months < 0:
        typer.echo("--months can't be negative.")
        raise typer.Exit(code=1)

    client = init_client()
    if not buckets.bucket_exists(client, bucket):
        typer.echo(f"Bucket '{bucket}' does not exist.")
        raise typer.Exit(code=1)

    cutoff = objects.months_ago(datetime.now(timezone.utc), months)
    cutoff_text = format_date(cutoff)
    old_label = f"older than {months} month(s)"

    missing = []
    to_delete = []  # (key, version) pairs
    for key in keys:
        versions = objects.list_object_versions(client, bucket, key)
        if not versions:
            typer.echo(f"Object 's3://{bucket}/{key}' does not exist.")
            missing.append(key)
            continue
        old = objects.select_old_versions(versions, cutoff)
        print_versions(bucket, key, versions, old, old_label)
        to_delete.extend((key, version) for version in old)

    if not to_delete:
        typer.echo(f"No old versions created before {cutoff_text}. Nothing to delete.")
    elif dry_run:
        typer.echo(f"Dry run: {len(to_delete)} version(s) created before {cutoff_text} would be deleted.")
    else:
        if not yes:
            typer.confirm(
                f"Permanently delete {len(to_delete)} version(s) created before {cutoff_text}?", abort=True
            )
        for key, version in to_delete:
            objects.delete_object_version(client, bucket, key, version["version_id"])
            typer.echo(f"Deleted version {version['version_id']} of s3://{bucket}/{key}.")
        typer.echo(f"{len(to_delete)} old version(s) deleted.")

    if missing:
        raise typer.Exit(code=1)


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


# ---------- host commands ----------

@host_app.command("deploy")
def host_deploy(
    bucket: Annotated[str, typer.Argument(help="Bucket for the website. Created if it does not exist.")],
    path: Annotated[
        Optional[Path],
        typer.Argument(
            help="index.html, or a folder with index.html at the top (for React: the dist/ folder). "
            "Or pass it with --source.",
            exists=True,
        ),
    ] = None,
    source: Annotated[
        Optional[Path],
        typer.Option("--source", "-s", help="Same as PATH: the page or the project folder to host.", exists=True),
    ] = None,
    spa: Annotated[
        bool,
        typer.Option("--spa", help="Single page app (React): show index.html for unknown paths too."),
    ] = False,
    region: Annotated[
        Optional[str],
        typer.Option(help="Region for a new bucket, e.g. eu-central-1. Default: AWS_REGION from .env."),
    ] = None,
    error_document: Annotated[
        Optional[str],
        typer.Option(
            "--error-document",
            help="Custom 'not found' page, e.g. error.html. It must be in the folder, or next to the "
            "deployed file (then it is uploaded too).",
        ),
    ] = None,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the website in the browser afterwards.")
    ] = False,
):
    """Host a page or a folder as a public website in one step: create the bucket if needed,
    make it publicly readable (replaces its bucket policy), turn on website hosting, upload."""
    if (path is None) == (source is None):
        typer.echo("Pass the website once: as PATH or with --source.")
        raise typer.Exit(code=1)
    path = path or source

    keys, url = website.host_static_website(
        init_client(region=region), bucket, path, spa=spa, error_document=error_document
    )
    typer.echo(f"Uploaded {len(keys)} file(s) to '{bucket}'.")
    show_website_url(url, open_browser)


def show_website_url(url, open_browser=False):
    typer.echo(f"Website: {url}")
    if open_browser:
        typer.launch(url)


@host_app.command("config")
def host_config(
    bucket: Annotated[str, typer.Argument(help="Name of the bucket.")],
    enable: Annotated[bool, typer.Option("--enable", help="Turn static website hosting on.")] = False,
    disable: Annotated[bool, typer.Option("--disable", help="Turn static website hosting off.")] = False,
    spa: Annotated[
        bool, typer.Option("--spa", help="With --enable: show index.html for unknown paths too (React).")
    ] = False,
    error_document: Annotated[
        Optional[str],
        typer.Option("--error-document", help="With --enable: key of a custom 'not found' page, e.g. error.html."),
    ] = None,
    open_browser: Annotated[
        bool, typer.Option("--open", help="With --enable: open the website in the browser.")
    ] = False,
):
    """Turn static website hosting on with --enable (index.html) or off with --disable.
    The files and the bucket policy are not changed."""
    if enable == disable:
        typer.echo("Use either --enable or --disable.")
        raise typer.Exit(code=1)
    if spa and error_document:
        typer.echo("Use either --spa or --error-document, not both.")
        raise typer.Exit(code=1)

    client = init_client()
    if not buckets.bucket_exists(client, bucket):
        typer.echo(f"Bucket '{bucket}' does not exist.")
        raise typer.Exit(code=1)

    error_document = website.INDEX_DOCUMENT if spa else error_document
    website.set_bucket_website_policy(client, bucket, switch=enable, error_document=error_document)
    if enable:
        typer.echo(f"Static website hosting turned on for '{bucket}'.")
        if error_document and objects.get_object_info(client, bucket, error_document) is None:
            typer.echo(f"Warning: '{error_document}' is not in the bucket yet. Upload it with 'host upload'.")
        show_website_url(website.website_url(bucket, buckets.get_bucket_region(client, bucket)), open_browser)
    else:
        typer.echo(f"Static website hosting turned off for '{bucket}'.")


@host_app.command("upload")
def host_upload(
    bucket: Annotated[str, typer.Argument(help="Bucket of the website.")],
    path: Annotated[Path, typer.Argument(help="File or folder to upload.", exists=True)],
    prefix: Annotated[str, typer.Option(help="Upload under this folder, e.g. docs/. Default: bucket root.")] = "",
):
    """Upload website files (a file or a folder with subfolders) with the right Content-Type."""
    client = init_client()
    keys = website.static_web_page_file(client, bucket, path, prefix=prefix)
    if not keys:
        typer.echo(f"No files to upload in '{path}'.")
        return
    for key in keys:
        typer.echo(f"s3://{bucket}/{key}")
    typer.echo(f"Uploaded {len(keys)} file(s).")


@host_app.command("status")
def host_status(
    bucket: Annotated[str, typer.Argument(help="Name of the bucket.")],
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the website in the browser if hosting is on.")
    ] = False,
):
    """Show whether static website hosting is on, its index/error page and the website URL."""
    client = init_client()
    config = website.read_bucket_website(client, bucket)
    if config is None:
        typer.echo(f"Static website hosting is not enabled on '{bucket}'.")
        return
    typer.echo(f"Static website hosting is enabled on '{bucket}'.")
    typer.echo(f"  index page: {config['index']}")
    typer.echo(f"  error page: {config['error'] or '(S3 default 404 page)'}")
    show_website_url(website.website_url(bucket, buckets.get_bucket_region(client, bucket)), open_browser)


@host_app.command("delete")
def host_delete(
    bucket: Annotated[str, typer.Argument(help="Bucket of the website.")],
    purge: Annotated[
        bool, typer.Option("--purge", help="Also delete every object in the bucket (the website files).")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Take a website down: turn off website hosting, with --purge also delete all files.
    The bucket and its bucket policy are kept."""
    if not yes:
        question = f"Turn off the website on '{bucket}'"
        question += " and delete ALL objects in it?" if purge else "?"
        typer.confirm(question, abort=True)

    deleted = website.delete_website(init_client(), bucket, purge=purge)
    typer.echo(f"Static website hosting turned off for '{bucket}'.")
    if purge:
        typer.echo(f"Deleted {deleted} object(s).")
    else:
        typer.echo("The files are still in the bucket and public through its bucket policy. Use --purge to delete them.")


# ---------- detect commands ----------

def require_api_key(provider):
    """Return the API key of the provider from .env, or stop with a hint."""
    if provider not in detection.PROVIDERS:
        typer.echo(f"Provider must be one of {', '.join(detection.PROVIDERS)}, got '{provider}'.")
        raise typer.Exit(code=1)
    name = detection.PROVIDER_KEYS[provider]
    api_key = read_setting(name)
    if not api_key:
        typer.echo(f"Set {name} in .env first (free key for api-ninjas: https://api-ninjas.com).")
        raise typer.Exit(code=1)
    return api_key


def print_detections(detections, min_confidence):
    if not detections:
        typer.echo(f"No objects found with confidence >= {min_confidence}.")
        return
    for item in detections:
        x1, y1, x2, y2 = item["box"]
        typer.echo(f"{item['label']:<15} {item['confidence']:.2f}  box ({x1}, {y1}) - ({x2}, {y2})")
    typer.echo(f"{len(detections)} object(s) found.")


@detect_app.command("deploy")
def detect_deploy(
    bucket: Annotated[str, typer.Argument(help="Bucket whose new images should be analysed.")],
    function_name: Annotated[
        str, typer.Option("--function-name", help="Name of the Lambda function.")
    ] = detection.DEFAULT_FUNCTION_NAME,
    provider: Annotated[
        str, typer.Option(help="Detection API: 'api-ninjas' (free, no card) or 'huggingface'.")
    ] = "api-ninjas",
    role_arn: Annotated[
        Optional[str],
        typer.Option(help="IAM role of the function. Default: LAMBDA_ROLE_ARN from .env, else LabRole (AWS Academy)."),
    ] = None,
):
    """Create (or update) the object detection Lambda function in the bucket's region and call it
    for every new .jpg / .jpeg / .png in the bucket. The API key from .env goes into its settings."""
    api_key = require_api_key(provider)
    s3_client = init_client()
    if not buckets.bucket_exists(s3_client, bucket):
        typer.echo(f"Bucket '{bucket}' does not exist.")
        raise typer.Exit(code=1)

    region = buckets.get_bucket_region(s3_client, bucket)
    arn, created = detection.deploy_object_detection(
        s3_client,
        init_client(region=region, service="lambda"),
        init_client(region=region, service="sts"),
        bucket,
        provider,
        api_key,
        role_arn=role_arn or read_setting("LAMBDA_ROLE_ARN"),
        function_name=function_name,
    )
    typer.echo(f"Lambda function '{function_name}' {'created' if created else 'updated'} in {region} ({provider}).")
    typer.echo(f"  {arn}")
    typer.echo(f"New .jpg / .jpeg / .png files in '{bucket}' now get a .json result next to them.")
    typer.echo(f"Try it: s3-manager detect image {bucket} photo.jpg")


@detect_app.command("image")
def detect_image(
    bucket: Annotated[str, typer.Argument(help="Bucket connected with 'detect deploy'.")],
    path: Annotated[Path, typer.Argument(help="Local .jpg / .jpeg / .png image.", exists=True, dir_okay=False)],
    key: Annotated[Optional[str], typer.Option(help="Object key in S3. Defaults to the file name.")] = None,
    timeout: Annotated[int, typer.Option(help="Seconds to wait for the result.")] = 60,
    min_confidence: Annotated[
        float, typer.Option(help="Only show objects with at least this confidence (0-1).")
    ] = 0.5,
):
    """Upload an image, wait until the Lambda function saved its .json result, and show the objects."""
    key, result = detection.detect_image(init_client(), path, bucket, key=key, timeout=timeout)
    typer.echo(f"Uploaded s3://{bucket}/{key}, result in s3://{bucket}/{detection.result_key(key)}")
    print_detections(detection.normalize_detections(result, min_confidence), min_confidence)


@detect_app.command("local")
def detect_local(
    path: Annotated[Path, typer.Argument(help="Local image.", exists=True, dir_okay=False)],
    provider: Annotated[
        str, typer.Option(help="Detection API: 'api-ninjas' (free, no card) or 'huggingface'.")
    ] = "api-ninjas",
    min_confidence: Annotated[
        float, typer.Option(help="Only show objects with at least this confidence (0-1).")
    ] = 0.5,
):
    """Send an image straight to the detection API, without AWS. Good for testing the API key."""
    api_key = require_api_key(provider)
    result = detection.query_detection_api(path.read_bytes(), provider, api_key)
    print_detections(detection.normalize_detections(result, min_confidence), min_confidence)


@detect_app.command("delete")
def detect_delete(
    bucket: Annotated[str, typer.Argument(help="Bucket connected with 'detect deploy'.")],
    function_name: Annotated[
        str, typer.Option("--function-name", help="Name of the Lambda function.")
    ] = detection.DEFAULT_FUNCTION_NAME,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
):
    """Remove the image triggers from the bucket and delete the Lambda function.
    The images and .json results stay in the bucket."""
    if not yes:
        typer.confirm(f"Delete Lambda function '{function_name}' and its triggers on '{bucket}'?", abort=True)

    s3_client = init_client()
    region = buckets.get_bucket_region(s3_client, bucket)
    removed, deleted = detection.remove_object_detection(
        s3_client, init_client(region=region, service="lambda"), bucket, function_name
    )
    typer.echo(f"Removed {removed} trigger(s) from '{bucket}'.")
    if deleted:
        typer.echo(f"Lambda function '{function_name}' deleted.")
    else:
        typer.echo(f"Lambda function '{function_name}' does not exist.")


# ---------- quote command ----------

@app.command("quote", cls=OptionalInspireCommand)
def quote_cmd(
    bucket: Annotated[
        Optional[str], typer.Argument(help="Bucket to save the quote in (only needed with --save).")
    ] = None,
    inspire: Annotated[
        Optional[str],
        typer.Option(
            "--inspire",
            metavar="[AUTHOR]",
            help='Show a random quote, or a random quote by AUTHOR: --inspire "Nelson Mandela".',
        ),
    ] = None,
    save: Annotated[bool, typer.Option("--save", help="Also save the quote as .json in BUCKET.")] = False,
    key: Annotated[
        Optional[str], typer.Option(help=f"Object key for --save. Default: {quotes.QUOTES_FOLDER}/<author>/<id>.json")
    ] = None,
):
    """Show an inspirational quote with --inspire, and save it to a bucket with --save.
    Put BUCKET before --inspire: 'quote my-bucket --inspire "Nelson Mandela" --save'."""
    if inspire is None:
        typer.echo('Pass --inspire for a random quote, or --inspire "AUTHOR" for a quote by that author.')
        raise typer.Exit(code=1)
    if save and not bucket:
        typer.echo('Pass the bucket before --inspire to save the quote: quote BUCKET --inspire --save')
        raise typer.Exit(code=1)

    client = None
    if save:
        # Check the bucket first, so a wrong name fails before the API call.
        client = init_client()
        if not buckets.bucket_exists(client, bucket):
            typer.echo(f"Bucket '{bucket}' does not exist.")
            raise typer.Exit(code=1)

    quote = quotes.get_quote(inspire or None)
    typer.echo(quotes.format_quote(quote))

    if save:
        saved_key = quotes.save_quote(client, bucket, quote, key=key)
        typer.echo(f"Saved to s3://{bucket}/{saved_key}")
