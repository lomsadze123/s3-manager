"""Command-line interface for s3-manager.

This module only handles user interaction (arguments, prompts, output).
The actual S3 work is done in buckets.py, objects.py and policies.py.
"""

from pathlib import Path
from typing import Annotated, Optional

import typer

from s3_manager import buckets, objects
from s3_manager.client import init_client

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
    typer.echo(f"TODO: set ACL '{acl}' on '{bucket}/{key}'")


# ---------- policy commands ----------

@policy_app.command("generate-public-read")
def policy_generate(
    bucket: Annotated[str, typer.Argument(help="Bucket the policy is for.")],
):
    """Print a public-read bucket policy as JSON (does not apply it)."""
    typer.echo(f"TODO: generate public-read policy for '{bucket}'")


@policy_app.command("create")
def policy_create(
    bucket: Annotated[str, typer.Argument(help="Bucket to apply the policy to.")],
):
    """Apply a public-read bucket policy."""
    typer.echo(f"TODO: apply public-read policy to '{bucket}'")


@policy_app.command("get")
def policy_get(
    bucket: Annotated[str, typer.Argument(help="Bucket to read the policy from.")],
):
    """Show the current bucket policy."""
    typer.echo(f"TODO: read policy of '{bucket}'")


@policy_app.command("set-public")
def policy_set_public(
    bucket: Annotated[str, typer.Argument(help="Bucket to make public.")],
):
    """Turn off Block Public Access and apply a public-read policy."""
    typer.echo(f"TODO: make '{bucket}' public")
