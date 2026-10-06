# s3-manager

A small command-line tool for working with Amazon S3 and other S3-compatible storage (MinIO, LocalStack, ...).

I built this project as an assignment for my master's DevOps course. The goal was to practice
Python project setup with Poetry, configuration with environment variables, logging, and working
with AWS S3 through boto3. I tried to keep the code simple and readable rather than "enterprise-grade".

## Features

- **Buckets:** list, create, check if a bucket exists, create only if missing (`ensure`), and delete
  only if it exists (with confirmation)
- **Uploads:** upload a local file, or download a file from a URL and upload it to S3
- **File type validation:** URL downloads are checked with python-magic, only `.bmp`, `.jpg`,
  `.jpeg`, `.png`, `.webp` and `.mp4` are accepted
- **Bucket policies:** generate a public-read policy (whole bucket or only some folders), apply it,
  read the current policy, and apply one only if the bucket has none (`policy ensure`)
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
| [python-magic](https://github.com/ahupp/python-magic) | detects the real file type of downloads (wrapper around the `libmagic` C library) |
| `logging` (standard library) | operation logs |
| [pytest](https://docs.pytest.org/) | unit tests |

## Installation

You need Python 3.11+, Poetry, and the `libmagic` system library (used by python-magic):

```bash
brew install libmagic          # macOS
sudo apt install libmagic1     # Debian / Ubuntu
```

Then:

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
| `AWS_SESSION_TOKEN` | no | Only for temporary credentials (e.g. AWS Academy / Learner Lab). Leave empty for IAM user keys. |
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
poetry run s3-manager bucket ensure my-unique-bucket-2026   # already exists -> nothing happens

# 2. upload a local file and files from the internet
poetry run s3-manager object upload ./photo.jpg my-unique-bucket-2026
poetry run s3-manager object upload ./photo.jpg my-unique-bucket-2026 --key dev/photo.jpg
poetry run s3-manager object upload-url https://www.python.org/static/img/python-logo.png my-unique-bucket-2026
poetry run s3-manager object upload-url https://picsum.photos/400 my-unique-bucket-2026 --key test/random
#   -> no extension in the key, so it is saved as test/random.jpg

# 3. make only the dev/ and test/ folders public (skipped if the bucket already has a policy)
poetry run s3-manager policy generate-public-read my-unique-bucket-2026 --prefix dev --prefix test  # preview
poetry run s3-manager policy ensure my-unique-bucket-2026
poetry run s3-manager policy get my-unique-bucket-2026

# the file is now available at:
# https://my-unique-bucket-2026.s3.<region>.amazonaws.com/dev/photo.jpg

# 4. clean up (the bucket must be empty before deleting)
poetry run s3-manager bucket delete my-unique-bucket-2026
```

Example output:

```text
$ poetry run s3-manager bucket create my-unique-bucket-2026
INFO: Creating bucket 'my-unique-bucket-2026' in region eu-central-1...
INFO: Bucket 'my-unique-bucket-2026' created successfully.
Bucket 'my-unique-bucket-2026' created.

$ poetry run s3-manager bucket ensure my-unique-bucket-2026
INFO: Bucket 'my-unique-bucket-2026' already exists, skipping creation.
Bucket 'my-unique-bucket-2026' already exists.

$ poetry run s3-manager bucket delete bucket-that-does-not-exist
Bucket 'bucket-that-does-not-exist' does not exist. Nothing to delete.

$ poetry run s3-manager bucket delete some-bucket -y
INFO: Deleting bucket 'some-bucket'...
ERROR: The bucket is not empty. Delete its objects first. (BucketNotEmpty)

$ poetry run s3-manager object upload-url https://www.python.org/ my-unique-bucket-2026
INFO: Downloading https://www.python.org/...
INFO: Downloaded 52508 bytes, detected type: text/html.
ERROR: Unsupported file type 'text/html'. Allowed: .bmp, .jpg, .jpeg, .png, .webp, .mp4.
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
| `bucket ensure NAME [--enable-acl]` | Create the bucket only if it does not exist, otherwise report that it exists |
| `bucket delete NAME [-y]` | Delete an empty bucket if it exists, otherwise report that it does not exist. Asks for confirmation unless `-y` is given. |
| `bucket exists NAME` | Check if a bucket exists (exit code `0` = yes, `1` = no) |
| `object upload PATH BUCKET [--key KEY]` | Upload a local file. Key defaults to the file name. |
| `object upload-url URL BUCKET [--key KEY]` | Download a file from a URL and upload it to S3. Only `.bmp`, `.jpg`, `.jpeg`, `.png`, `.webp`, `.mp4` are accepted. |
| `object set-acl BUCKET KEY [--acl public-read\|private]` | Set the ACL of one object |
| `policy generate-public-read BUCKET [--prefix P ...]` | Print a public-read policy (does not change anything). With `--prefix`, only those folders are public. |
| `policy create BUCKET` | Apply the public-read policy to the bucket |
| `policy get BUCKET` | Show the current bucket policy |
| `policy ensure BUCKET [--prefix P ...]` | If the bucket has no policy, turn off Block Public Access and make the given folders public (default: `dev/` and `test/`). If it has one, report it and change nothing. |
| `policy set-public BUCKET` | Turn off Block Public Access and apply the public-read policy |

Every command has its own help, e.g. `poetry run s3-manager object upload --help`.

## Lecture functions

All functions from the lecture are implemented. Each one is a plain function that receives the
boto3 client, and the CLI commands call them:

| Function | Module | Used by command |
|---|---|---|
| `init_client()` | `client.py` | every command |
| `list_buckets()` | `buckets.py` | `bucket list` |
| `create_bucket()` | `buckets.py` | `bucket create`, `bucket ensure` |
| `delete_bucket()` | `buckets.py` | `bucket delete` |
| `bucket_exists()` | `buckets.py` | `bucket exists`, `bucket ensure`, `bucket delete` |
| `download_file_and_upload_to_s3()` | `objects.py` | `object upload-url` |
| `set_object_access_policy()` | `objects.py` | `object set-acl` |
| `generate_public_read_policy()` | `policies.py` | `policy generate-public-read`, `policy create`, `policy ensure` |
| `create_bucket_policy()` | `policies.py` | `policy create`, `policy set-public`, `policy ensure` |
| `read_bucket_policy()` | `policies.py` | `policy get`, `policy ensure` |

Differences from the lecture code:

- Functions don't `print` or return `False` on errors. They log with `logging` and let the
  exception go up, and the CLI turns it into one readable error line.
- `download_file_and_upload_to_s3()` checks the file type with python-magic instead of always
  sending `image/jpg` (see [File type validation](#file-type-validation)).
- The lecture fixes the permissions issue with `delete_public_access_block()`. This tool uses
  `put_public_access_block()` with all four settings set to `False`, which has the same effect but
  keeps an explicit configuration on the bucket. It is a separate step (`policy set-public`,
  `policy ensure`) rather than hidden inside `create_bucket_policy()`.

## Project structure

```text
s3-manager/
├── src/s3_manager/
│   ├── cli.py         # Typer commands, logging setup, error handling
│   ├── client.py      # init_client(): reads .env and creates the boto3 client
│   ├── buckets.py     # list_buckets, create_bucket, delete_bucket, bucket_exists
│   ├── objects.py     # upload_file, download_file_and_upload_to_s3, set_object_access_policy
│   └── policies.py    # generate_public_read_policy, create_bucket_policy, read_bucket_policy
├── tests/             # pytest unit tests for each module and the CLI (no AWS account needed)
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
  only displays it as a folder. Keys also have no leading slash, so a policy for `/dev/*` would
  match nothing. `--prefix /dev`, `--prefix dev` and `--prefix dev/` all mean the `dev/` folder.

### File type validation

`download_file_and_upload_to_s3()` only accepts `.bmp`, `.jpg`, `.jpeg`, `.png`, `.webp` and `.mp4`.
The URL and the server's `Content-Type` header can both be wrong (for example an error page served
as `image/jpeg`), so the type is detected from the downloaded bytes themselves. python-magic reads
the first bytes of the file (the "magic numbers", e.g. every PNG starts with `\x89PNG`) and returns
a MIME type such as `image/png`. Then:

1. If the MIME type is not one of `image/bmp`, `image/jpeg`, `image/png`, `image/webp`, `video/mp4`,
   the upload is rejected before anything is sent to S3.
2. If the key has no extension (e.g. `https://picsum.photos/400`), the right one is added: `400.jpg`.
3. If the key has an extension that doesn't match the content (a JPEG saved as `photo.png`), the
   upload is rejected.
4. The detected MIME type is stored as the object's `Content-Type`, so browsers display it correctly.

### Bucket policies vs object ACLs

There are two ways to make objects public:

- A **bucket policy** is a JSON document attached to the bucket. The one this tool generates
  allows everyone (`"Principal": "*"`) to read (`"s3:GetObject"`) all objects
  (`"arn:aws:s3:::BUCKET/*"`), or with `--prefix` only objects in some folders
  (`"arn:aws:s3:::BUCKET/dev/*"`, `"arn:aws:s3:::BUCKET/test/*"`). This is the method AWS recommends.
- An **object ACL** is an older, per-object setting such as `public-read`. New buckets have ACLs
  disabled, so this only works on buckets created with `--enable-acl`
  (`ObjectOwnership=ObjectWriter`).

Since 2023, new buckets also have **Block Public Access** turned on, and AWS rejects any public
policy or ACL with `AccessDenied` while it is on. That is why `policy create` alone fails on a new
bucket and `policy set-public` and `policy ensure` first turn Block Public Access off. If it is also enabled at the
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
For the URL download, `requests.get` is patched so that no real HTTP request happens, and it returns
the first bytes of real PNG / JPEG / MP4 / HTML files so python-magic runs for real. CLI commands are
tested with Typer's `CliRunner` and the same fake client.

What is covered:

- the generated policy JSON (whole bucket and prefix-only) and that it is sent to AWS as a JSON string
- `read_bucket_policy` returning `None` when the bucket has no policy
- `ensure_bucket_policy` skipping a bucket with a policy, and otherwise turning off Block Public
  Access and applying the prefix policy
- region handling and `--enable-acl` in `create_bucket`
- `bucket_exists` returning `False` for 404 and raising for 403
- `ensure_bucket` creating a missing bucket and skipping an existing one
- `bucket delete`: reporting a missing bucket, deleting an existing one, and keeping it when the
  confirmation is declined
- the object key and Content-Type for both upload functions
- file type validation: allowed types accepted, missing extension added, unsupported type and
  wrong extension rejected before uploading
- rejecting an unknown ACL before calling AWS

I also tested all commands manually against a real AWS account.

## Possible future improvements

- `bucket delete --force` to delete all objects before deleting the bucket
- `object list` and `object delete` commands
- streaming large downloads directly to S3 instead of keeping them in memory
- uploading a custom policy from a JSON file
- a `--dry-run` option that shows what would happen without changing anything
- integration tests with [moto](https://github.com/getmoto/moto) or a local MinIO container
- the same file type validation for local uploads (`object upload`)

## Troubleshooting

**`ImportError: failed to find libmagic. Check your installation`.**
python-magic is only a wrapper, the actual detection is done by the `libmagic` C library, which
Poetry can't install. Install it with `brew install libmagic` (macOS) or
`sudo apt install libmagic1` (Debian / Ubuntu).

**`Access denied. The bucket may belong to another AWS account. (403)` for a bucket you never created.**
Bucket names are global, so common names like `my-bucket` are already taken by someone else. Pick a
unique name, e.g. with your name and a date.

**`ModuleNotFoundError: No module named 's3_manager'` on macOS with Python 3.13+.**
Python skips `.pth` files that have the macOS "hidden" file flag, and on some setups files inside an
in-project `.venv` folder get this flag. I fixed it by using Poetry's default virtualenv location
(outside the project folder) instead of `virtualenvs.in-project = true`.
