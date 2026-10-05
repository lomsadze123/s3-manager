# s3-manager

A small command-line tool for working with Amazon S3 and other S3-compatible storage (MinIO, LocalStack, ...).

I built this project as an assignment for my master's DevOps course. The goal was to practice
Python project setup with Poetry, configuration with environment variables, logging, and working
with AWS S3 through boto3. I tried to keep the code simple and readable rather than "enterprise-grade".

## Features

- **Buckets:** list, create, delete (with confirmation) and check if a bucket exists
- **Uploads:** upload a local file, or download a file from a URL and upload it to S3
- **Bucket policies:** generate a public-read policy, apply it, and read the current policy
- **Public access:** turn off *Block Public Access* and make a bucket publicly readable in one command
- **Object ACLs:** make a single object `public-read` or `private`
- **Configuration** through a `.env` file (credentials are never stored in the code)
- **Logging** of every operation, with a `--verbose` mode for debugging
- **Readable errors** instead of Python tracebacks (e.g. `ERROR: The bucket does not exist. (NoSuchBucket)`)
- Works with any S3-compatible service through an optional endpoint URL

## Technologies

| Tool | Used for |
|---|---|
| Python 3.11+ | language |
| [Poetry](https://python-poetry.org/) | dependency management, virtual environment, packaging |
| [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) | AWS SDK for Python, talks to the S3 API |
| [python-dotenv](https://pypi.org/project/python-dotenv/) | loads configuration from a `.env` file |
| [Typer](https://typer.tiangolo.com/) | builds the CLI from Python functions and type hints |
| [requests](https://requests.readthedocs.io/) | downloads files from URLs |
| `logging` (standard library) | operation logs |
| [pytest](https://docs.pytest.org/) | unit tests |

## Installation

You need Python 3.11+ and Poetry installed.

```bash
git clone https://github.com/lomsadze123/s3-manager.git
cd s3-manager
poetry install
```

`poetry install` creates a virtual environment and installs the exact dependency versions from
`poetry.lock`, plus the project itself, so the `s3-manager` command becomes available:

```bash
poetry run s3-manager --help
```

## Environment variables

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | yes* | Access key of an IAM user |
| `AWS_SECRET_ACCESS_KEY` | yes* | Secret key of the same IAM user |
| `AWS_REGION` | no | Region for new buckets, e.g. `eu-central-1` (default: `us-east-1`) |
| `S3_ENDPOINT_URL` | no | Endpoint of an S3-compatible service, e.g. `http://localhost:9000` for MinIO. Leave empty for AWS. |

\* If the keys are not set, boto3 falls back to its normal credential lookup (for example
`~/.aws/credentials` created by `aws configure`).

Variables that are already set in the shell take priority over `.env`, so you can override a value
for one command: `AWS_REGION=eu-west-1 poetry run s3-manager bucket list`.

**Getting credentials:** in the AWS console, create an IAM user (not your root account), attach the
`AmazonS3FullAccess` policy, and create an access key for it under *Security credentials*.

> The `.env` file contains secrets and is listed in `.gitignore`. Only `.env.example` (without values)
> is committed.

## Usage examples

A full walkthrough, from creating a bucket to making a file public:

```bash
# 1. create a bucket (names are global, so pick a unique one)
poetry run s3-manager bucket create my-unique-bucket-2026 --enable-acl
poetry run s3-manager bucket exists my-unique-bucket-2026

# 2. upload a local file and a file from the internet
poetry run s3-manager object upload ./photo.jpg my-unique-bucket-2026
poetry run s3-manager object upload ./photo.jpg my-unique-bucket-2026 --key images/photo.jpg
poetry run s3-manager object upload-url https://www.python.org/static/img/python-logo.png my-unique-bucket-2026

# 3. look at the policy before applying it, then make the bucket public
poetry run s3-manager policy generate-public-read my-unique-bucket-2026
poetry run s3-manager policy set-public my-unique-bucket-2026
poetry run s3-manager policy get my-unique-bucket-2026

# the file is now available at:
# https://my-unique-bucket-2026.s3.<region>.amazonaws.com/photo.jpg

# 4. clean up (the bucket must be empty before deleting)
poetry run s3-manager bucket delete my-unique-bucket-2026
```

Example output:

```text
$ poetry run s3-manager bucket create my-unique-bucket-2026
INFO: Creating bucket 'my-unique-bucket-2026' in region eu-central-1...
INFO: Bucket 'my-unique-bucket-2026' created successfully.
Bucket 'my-unique-bucket-2026' created.

$ poetry run s3-manager bucket delete some-bucket -y
INFO: Deleting bucket 'some-bucket'...
ERROR: The bucket is not empty. Delete its objects first. (BucketNotEmpty)
```

Logs (`INFO:`, `ERROR:`) are written to **stderr** and results to **stdout**, so results can be
redirected without the logs:

```bash
poetry run s3-manager policy get my-unique-bucket-2026 > policy.json
```

## CLI commands

Global option: `-v` / `--verbose` shows debug logs and full error details. It goes before the
command: `s3-manager -v bucket list`.

| Command | Description |
|---|---|
| `bucket list` | List all buckets |
| `bucket create NAME [--enable-acl]` | Create a bucket in `AWS_REGION`. `--enable-acl` allows object ACLs. |
| `bucket delete NAME [-y]` | Delete an empty bucket. Asks for confirmation unless `-y` is given. |
| `bucket exists NAME` | Check if a bucket exists (exit code `0` = yes, `1` = no) |
| `object upload PATH BUCKET [--key KEY]` | Upload a local file. Key defaults to the file name. |
| `object upload-url URL BUCKET [--key KEY]` | Download a file from a URL and upload it to S3 |
| `object set-acl BUCKET KEY [--acl public-read\|private]` | Set the ACL of one object |
| `policy generate-public-read BUCKET` | Print a public-read policy (does not change anything) |
| `policy create BUCKET` | Apply the public-read policy to the bucket |
| `policy get BUCKET` | Show the current bucket policy |
| `policy set-public BUCKET` | Turn off Block Public Access and apply the public-read policy |

Every command has its own help, e.g. `poetry run s3-manager object upload --help`.

## Project structure

```text
s3-manager/
├── src/s3_manager/
│   ├── cli.py         # Typer commands, logging setup, error handling
│   ├── client.py      # init_client(): reads .env and creates the boto3 client
│   ├── buckets.py     # list_buckets, create_bucket, delete_bucket, bucket_exists
│   ├── objects.py     # upload_file, download_file_and_upload_to_s3, set_object_access_policy
│   └── policies.py    # generate_public_read_policy, create_bucket_policy, read_bucket_policy
├── tests/             # pytest unit tests (no AWS account needed)
├── .env.example       # template for the .env file
├── pyproject.toml     # project metadata and dependencies
└── poetry.lock        # exact versions of all dependencies
```

## How it works

### Layers

The code is split into three simple layers:

1. **`cli.py`** is the only part that talks to the user. It parses arguments, asks for confirmation,
   prints results and turns errors into short messages.
2. **`buckets.py`, `objects.py`, `policies.py`** contain plain functions that do the S3 work. They
   don't print anything; they return data or raise exceptions. Each function receives the boto3
   client as its first argument, which makes them easy to test with a fake client.
3. **`client.py`** is the only place that reads configuration from the environment.

For example, `s3-manager bucket create my-bucket` goes through these steps:

```text
cli.bucket_create()  →  client.init_client()  →  buckets.create_bucket(client, "my-bucket")  →  boto3  →  S3 API
```

### boto3 and the S3 client

`init_client()` loads `.env` with python-dotenv and creates a client with `boto3.client("s3", ...)`.
Creating the client does not connect to AWS yet. Each method call (for example
`client.create_bucket(...)`) becomes one signed HTTPS request to the S3 API, and the response
comes back as a Python dict. Because many storage services implement the same API, setting
`endpoint_url` is enough to use MinIO or LocalStack instead of AWS.

### A few S3 details I had to handle

- **Bucket names are global.** They are unique across all AWS accounts, so `my-bucket` is already
  taken. `head_bucket` returns 404 if a bucket doesn't exist and 403 if it exists but belongs to
  someone else, so `bucket_exists()` only returns `False` for 404.
- **The us-east-1 exception.** In every region except `us-east-1`, `create_bucket` needs a
  `LocationConstraint`. In `us-east-1` it must be left out.
- **Content-Type.** S3 stores metadata with each object. I set `Content-Type` (from the file
  extension or the HTTP response header) so that browsers show images instead of downloading them.
- **Folders don't really exist.** A key like `images/photo.jpg` is just a string; the AWS console
  only displays it as a folder.

### Bucket policies vs object ACLs

There are two ways to make objects public:

- A **bucket policy** is a JSON document attached to the bucket. The one this tool generates
  allows everyone (`"Principal": "*"`) to read (`"s3:GetObject"`) all objects
  (`"arn:aws:s3:::BUCKET/*"`). This is the method AWS recommends.
- An **object ACL** is an older, per-object setting such as `public-read`. New buckets have ACLs
  disabled, so this only works on buckets created with `--enable-acl`
  (`ObjectOwnership=ObjectWriter`).

Since 2023, new buckets also have **Block Public Access** turned on, and AWS rejects any public
policy or ACL with `AccessDenied` while it is on. That is why `policy create` alone fails on a new
bucket and `policy set-public` first turns Block Public Access off. If it is also enabled at the
account level (S3 console → *Block Public Access settings for this account*), it must be turned off
there as well.

### Logging and errors

Every module creates its own logger with `logging.getLogger(__name__)`. Logging is configured only
once, in the CLI: our own logs are shown at `INFO` level (`DEBUG` with `--verbose`), while logs from
boto3 and other libraries are kept at `WARNING` because they are very noisy.

All commands run inside `main()`, which catches the expected error types and prints one readable line
with exit code `1`:

- `ClientError`: AWS returned an error. The error code (e.g. `BucketNotEmpty`) is mapped to a
  friendlier message.
- `NoCredentialsError`, `ParamValidationError` and other `BotoCoreError`s: problems before a
  response arrives (missing credentials, invalid input, network).
- `requests` errors: a download failed.
- `ValueError`: invalid input such as an unknown ACL.

Unexpected errors are not caught, so real bugs still show a full traceback.

## Testing

```bash
poetry run pytest -v
```

The tests run offline and don't need an AWS account. Instead of a real boto3 client, they pass a
`unittest.mock.MagicMock`, which records every method call. The tests then check that the right
request was sent, for example that `create_bucket` has no `LocationConstraint` in `us-east-1`.
For the URL download, `requests.get` is patched so that no real HTTP request happens.

What is covered:

- the generated policy JSON and that it is sent to AWS as a JSON string
- `read_bucket_policy` returning `None` when the bucket has no policy
- region handling and `--enable-acl` in `create_bucket`
- `bucket_exists` returning `False` for 404 and raising for 403
- the object key and Content-Type for both upload functions
- rejecting an unknown ACL before calling AWS

I also tested all commands manually against a real AWS account.

## Possible future improvements

- `bucket delete --force` to delete all objects before deleting the bucket
- `object list` and `object delete` commands
- streaming large downloads directly to S3 instead of keeping them in memory
- uploading a custom policy from a JSON file
- a `--dry-run` option that shows what would happen without changing anything
- integration tests with [moto](https://github.com/getmoto/moto) or a local MinIO container
- tests for the CLI layer with Typer's `CliRunner`

## Troubleshooting

**`ModuleNotFoundError: No module named 's3_manager'` on macOS with Python 3.13+.**
Python skips `.pth` files that have the macOS "hidden" file flag, and on some setups files inside an
in-project `.venv` folder get this flag. I fixed it by using Poetry's default virtualenv location
(outside the project folder) instead of `virtualenvs.in-project = true`.
