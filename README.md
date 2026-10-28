# s3-manager

A small command-line tool for working with Amazon S3 and other S3-compatible storage (MinIO, LocalStack, ...).

I built this project as an assignment for my master's DevOps course. The goal was to practice
Python project setup with Poetry, configuration with environment variables, logging, and working
with AWS S3 through boto3. I tried to keep the code simple and readable rather than "enterprise-grade".

## Features

- **Buckets:** list, create (in any region with `--region`, with versioning turned on right away
  with `-vers`), check if a bucket exists, create only if missing (`ensure`), and delete only if it
  exists (with confirmation)
- **List objects:** show every object in a bucket (or one folder) with its size and date (`object list`)
- **Uploads:** upload a local file, or download a file from a URL and upload it to S3
- **Small and large files:** small files are sent in one `put_object` request, large files with a
  multipart upload (part by part, aborted cleanly on failure). Chosen automatically by file size
  (default: from 100 MiB) or forced with `--method small|large`. boto3's managed transfers are
  available too: `--method upload_file|upload_fileobj`
- **File type validation:** URL downloads (always) and local uploads (with `--validate-mime`) are
  checked with python-magic, only `.bmp`, `.jpg`, `.jpeg`, `.png`, `.webp` and `.mp4` are accepted
- **Upload into a folder by type:** `object upload FILE BUCKET -bt` (`--by-type`) uploads a file of
  any extension into a folder named after its real type, detected from the content with
  python-magic (`photo.jpg` → `jpg/photo.jpg`, `report.pdf` → `pdf/report.pdf`, a `fake.jpg` that
  is really a PNG → `png/fake.jpg`). When magic only sees plain text or raw bytes, the file's own
  extension decides (`data.csv` → `csv/data.csv`); with no type and no extension it goes to `unknown/`
- **Default encryption:** show a bucket's default encryption or set it to SSE-S3 / AES256
  (`bucket encryption NAME -es --enable`)
- **Lifecycle policy:** delete objects automatically 120 days (or any number of days) after
  creation, for the whole bucket or one folder
- **Bucket policies:** generate a public-read policy (whole bucket or only some folders), apply it,
  read the current policy, and apply one only if the bucket has none (`policy ensure`). The lecture's
  "multiple" policy (list versions, read, change ACLs, delete) can be applied to test buckets
  (`policy create-multiple`)
- **Public access:** turn off *Block Public Access* and make a bucket publicly readable in one command
- **Object ACLs:** make a single object `public-read` or `private`
- **Delete a file:** pass the bucket, the key and the `-del` flag (`object file BUCKET KEY -del`) to
  delete one object; without the flag the command only shows its size and type
- **Versioning:** check whether a bucket has versioning enabled (`bucket versioning NAME -vs`),
  turn it on or suspend it, list the versions of a file with their creation dates
  (`object file BUCKET KEY -ver`), and upload the second-to-last version (`-rp`) or any chosen
  version (`-rbt VERSION_ID`) again as a new version
- **Delete old versions:** pass a bucket and one or more files
  (`object old-versions BUCKET KEY [KEY...]`) to list the versions of each file and permanently
  delete every version created more than 6 months ago (`--months N`). The latest version is always
  kept, `--dry-run` only shows what would be deleted
- **Organize by extension:** pass the bucket and the `-ext` flag (`bucket organize NAME -ext`) to
  move every file into a folder named after its extension (`image.jpg` → `jpg/image.jpg`,
  `demo.csv` → `csv/demo.csv`) and print how many files were moved per extension (`csv - 2`).
  With `--unknown`, files without an extension go to `unknown/`
- **Static website hosting:** `host deploy BUCKET PATH` turns a bucket into a public website in one
  step (create the bucket if needed, public-read policy, website hosting, upload) and prints the URL.
  It works for a single `index.html` and for a built React app (`--spa`). Every file gets the right
  `Content-Type` and `Cache-Control`. `host config`, `host upload` and `host status` do the single steps.
  My own extra arguments: `--error-document error.html` (custom 404 page), `--open` (open the site in
  the browser) and `host delete [--purge]` (take the site down, optionally deleting its files)
- **One-command hosting of a downloaded website:** `python main.py host BUCKET --source FOLDER`
  uploads a project folder (here the [do-community/html_demo_site](https://github.com/do-community/html_demo_site)
  source in `static_web_page/html_demo_site/`), sets the `WebsiteConfiguration`, makes the bucket
  publicly readable and prints the website URL. It is a shortcut for `host deploy`
- **Object detection with AWS Lambda:** `detect deploy BUCKET` creates a Lambda function and connects
  it to the bucket, so every new `.jpg` / `.jpeg` / `.png` is sent to the free
  [API Ninjas Object Detection](https://api-ninjas.com/api/objectdetection) API (or Hugging Face) and
  the result is saved next to the image as JSON (`cat.jpg` → `cat.json`). `detect image` uploads an
  image and prints the detected objects, `detect local` calls the API without AWS, `detect delete`
  removes everything again
- **Inspirational quotes:** `quote --inspire` prints a random quote from the
  [Quotable API](https://api.quotable.kurokeita.dev/), `quote --inspire "Nelson Mandela"` a quote by
  that author, and `quote BUCKET --inspire "Nelson Mandela" --save` also saves the quote as a `.json`
  object in the bucket (`quotes/nelson-mandela/<id>.json`)
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
| [python-magic](https://github.com/ahupp/python-magic) | detects the real file type of downloads and `--by-type` uploads (wrapper around the `libmagic` C library) |
| `logging` (standard library) | operation logs |
| [AWS Lambda](https://docs.aws.amazon.com/lambda/) + [urllib3](https://urllib3.readthedocs.io/) | the object detection function (`aws_lambda/object_detection.py`); urllib3 is already in the Lambda runtime, so no layer is needed |
| [API Ninjas Object Detection](https://api-ninjas.com/api/objectdetection) | free object detection API (no bank card); Hugging Face `facebook/detr-resnet-50` as an option |
| [pytest](https://docs.pytest.org/) | unit tests |
| [Node.js](https://nodejs.org/) + [Vite](https://vite.dev/) + [React](https://react.dev/) | only for building the React demo site in `static_web_page/react-app/` |

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
| `API_NINJAS_KEY` | for `detect` | Key from [api-ninjas.com](https://api-ninjas.com) (free, no bank card). `detect deploy` copies it into the Lambda function's environment variables. |
| `HF_API_TOKEN` | no | Hugging Face token, only for `detect ... --provider huggingface` |
| `LAMBDA_ROLE_ARN` | no | IAM role of the Lambda function. Empty: the account's `LabRole` (AWS Academy), or pass `--role-arn` |

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
poetry run s3-manager object upload ./photo.jpg my-unique-bucket-2026 --key dev/photo.jpg --validate-mime
poetry run s3-manager object upload ./video.mp4 my-unique-bucket-2026   # >= 100 MiB -> multipart
poetry run s3-manager object upload ./backup.zip my-unique-bucket-2026 --method large --part-size-mb 50
poetry run s3-manager object upload-url https://www.python.org/static/img/python-logo.png my-unique-bucket-2026
poetry run s3-manager object upload-url https://picsum.photos/400 my-unique-bucket-2026 --key test/random
#   -> no extension in the key, so it is saved as test/random.jpg
poetry run s3-manager object upload ./report.pdf my-unique-bucket-2026 --by-type   # -> pdf/report.pdf
poetry run s3-manager object upload ./fake.jpg my-unique-bucket-2026 -bt           # real PNG -> png/fake.jpg

# 3. make only the dev/ and test/ folders public (skipped if the bucket already has a policy)
poetry run s3-manager policy generate-public-read my-unique-bucket-2026 --prefix dev --prefix test  # preview
poetry run s3-manager policy ensure my-unique-bucket-2026
poetry run s3-manager policy get my-unique-bucket-2026

# the file is now available at:
# https://my-unique-bucket-2026.s3.<region>.amazonaws.com/dev/photo.jpg

# 4. delete every object automatically 120 days after it was uploaded
poetry run s3-manager lifecycle set my-unique-bucket-2026
poetry run s3-manager lifecycle get my-unique-bucket-2026

# 5. look at one file, then delete it with the -del flag
poetry run s3-manager object file my-unique-bucket-2026 dev/photo.jpg
poetry run s3-manager object file my-unique-bucket-2026 dev/photo.jpg -del

# 6. versioning: turn it on, upload the same key twice, list the versions, bring back the old one
poetry run s3-manager bucket versioning my-unique-bucket-2026 -vs              # never enabled
poetry run s3-manager bucket versioning my-unique-bucket-2026 --enable -vs
poetry run s3-manager object upload ./photo.jpg my-unique-bucket-2026 --key notes/photo.jpg
poetry run s3-manager object upload ./other.jpg my-unique-bucket-2026 --key notes/photo.jpg
poetry run s3-manager object file my-unique-bucket-2026 notes/photo.jpg -ver   # 2 versions
poetry run s3-manager object file my-unique-bucket-2026 notes/photo.jpg -rp    # photo.jpg is the latest again
poetry run s3-manager object file my-unique-bucket-2026 notes/photo.jpg -ver   # 3 versions
# delete versions older than 6 months of one or more files (the latest version is always kept)
poetry run s3-manager object old-versions my-unique-bucket-2026 notes/photo.jpg demo.csv --dry-run
poetry run s3-manager object old-versions my-unique-bucket-2026 notes/photo.jpg demo.csv   # asks first
# fresh test versions are never 6 months old; --months 0 treats every non-latest version as old
poetry run s3-manager object old-versions my-unique-bucket-2026 notes/photo.jpg --months 0 --dry-run

# 7. sort the files in the bucket root into folders by extension
poetry run s3-manager object upload ./image.jpg my-unique-bucket-2026
poetry run s3-manager object upload ./demo.csv my-unique-bucket-2026
poetry run s3-manager object upload ./users.csv my-unique-bucket-2026
poetry run s3-manager bucket organize my-unique-bucket-2026 -ext   # csv - 2, jpg - 1
poetry run s3-manager bucket organize my-unique-bucket-2026 -ext   # nothing left to move

# 8. extras from the Catch UP 2 lecture script
poetry run s3-manager bucket create my-versioned-bucket-2026 --region us-west-2 -vers   # other region + versioning
poetry run s3-manager bucket encryption my-unique-bucket-2026 --enable -es             # AES256 by default
poetry run s3-manager object list my-unique-bucket-2026                               # every key, size, date
poetry run s3-manager object list my-unique-bucket-2026 --prefix csv/
poetry run s3-manager object upload ./demo.csv my-unique-bucket-2026 --method upload_file
poetry run s3-manager object upload ./demo.csv my-unique-bucket-2026 --method upload_fileobj
poetry run s3-manager object file my-unique-bucket-2026 notes/photo.jpg -ver               # copy an id
poetry run s3-manager object file my-unique-bucket-2026 notes/photo.jpg -rbt <VERSION_ID>  # bring it back
poetry run s3-manager bucket organize my-unique-bucket-2026 -ext --unknown   # README -> unknown/README
poetry run s3-manager policy create-multiple my-unique-bucket-2026          # asks first: anyone can delete!

# 9. host a static website (see "Static website hosting" below)
poetry run s3-manager host deploy beka-site-2026 static_web_page/index.html   # page with my name
cd static_web_page/react-app && npm install && npm run build && cd ../..       # React -> dist/
poetry run s3-manager host deploy beka-react-2026 static_web_page/react-app/dist --spa
poetry run s3-manager host status beka-react-2026
poetry run s3-manager host config beka-react-2026 --disable                    # website off again
# my own arguments: custom 404 page, open the browser, take the site down
poetry run s3-manager host deploy beka-site-2026 static_web_page/index.html --error-document error.html --open
#   -> http://beka-site-2026.s3-website-us-east-1.amazonaws.com/missing shows static_web_page/error.html
poetry run s3-manager host status beka-site-2026 --open
poetry run s3-manager host delete beka-site-2026            # hosting off, files stay
poetry run s3-manager host delete beka-site-2026 --purge    # hosting off and every file deleted
# a downloaded website (github.com/do-community/html_demo_site) with ONE command, as a script:
poetry run python main.py host beka-demo-site-2026 --source static_web_page/html_demo_site
poetry run python main.py host delete beka-demo-site-2026 --purge -y   # clean up

# 10. object detection with Lambda (see "Object detection with AWS Lambda" below)
poetry run s3-manager detect local ./photo.jpg                # test the API key, no AWS
poetry run s3-manager bucket create beka-detect-2026
poetry run s3-manager detect deploy beka-detect-2026          # function + permission + trigger
poetry run s3-manager detect image beka-detect-2026 ./photo.jpg   # upload, wait for photo.json, print it
poetry run s3-manager object list beka-detect-2026            # photo.jpg and photo.json
poetry run s3-manager detect delete beka-detect-2026          # trigger + function removed

# 11. inspirational quotes (see "Inspirational quotes" below)
poetry run s3-manager quote --inspire                         # random quote, no AWS needed
poetry run s3-manager quote --inspire "Nelson Mandela"        # quote by one author
poetry run s3-manager quote my-unique-bucket-2026 --inspire "Nelson Mandela" --save
#   -> saved to s3://my-unique-bucket-2026/quotes/nelson-mandela/<id>.json
poetry run s3-manager quote my-unique-bucket-2026 --inspire --save --key quotes/today.json

# 12. clean up (the bucket must be empty before deleting)
poetry run s3-manager bucket delete my-unique-bucket-2026
```

Example output:

```text
$ poetry run s3-manager bucket create my-unique-bucket-2026
INFO: Creating bucket 'my-unique-bucket-2026' in region eu-central-1...
INFO: Bucket 'my-unique-bucket-2026' created successfully.
Bucket 'my-unique-bucket-2026' created in eu-central-1.

$ poetry run s3-manager bucket ensure my-unique-bucket-2026
INFO: Bucket 'my-unique-bucket-2026' already exists, skipping creation.
Bucket 'my-unique-bucket-2026' already exists.

$ poetry run s3-manager bucket delete bucket-that-does-not-exist
Bucket 'bucket-that-does-not-exist' does not exist. Nothing to delete.

$ poetry run s3-manager bucket delete some-bucket -y
INFO: Deleting bucket 'some-bucket'...
ERROR: The bucket is not empty. Delete its objects first. (BucketNotEmpty)

$ poetry run s3-manager object upload ./video.mp4 my-unique-bucket-2026
INFO: File is 120.0 MiB, using the 'large' upload method.
INFO: Starting multipart upload of 'video.mp4' to s3://my-unique-bucket-2026/video.mp4 (12 parts of 10 MiB)...
INFO: Uploaded part 1/12.
...
INFO: Uploaded part 12/12.
INFO: Upload finished.
Uploaded to s3://my-unique-bucket-2026/video.mp4 (large upload)

$ poetry run s3-manager object upload ./fake.jpg my-unique-bucket-2026 --by-type
INFO: Detected type image/png, uploading into folder 'png/'.
INFO: File is 0.0 MiB, using the 'small' upload method.
INFO: Uploading 'fake.jpg' to s3://my-unique-bucket-2026/png/fake.jpg in one request...
INFO: Upload finished.
Uploaded to s3://my-unique-bucket-2026/png/fake.jpg (small upload)

$ poetry run s3-manager lifecycle set my-unique-bucket-2026
INFO: Setting lifecycle rule 'expire-all-after-120-days' on 'my-unique-bucket-2026'...
INFO: Lifecycle policy applied.
Lifecycle policy set on 'my-unique-bucket-2026': objects in the bucket are deleted 120 days after creation.

$ poetry run s3-manager object file my-unique-bucket-2026 dev/photo.jpg
s3://my-unique-bucket-2026/dev/photo.jpg exists (48213 bytes, image/jpeg).
Pass -del to delete it.

$ poetry run s3-manager object file my-unique-bucket-2026 dev/photo.jpg -del
INFO: Deleting s3://my-unique-bucket-2026/dev/photo.jpg...
INFO: Object deleted.
Object 's3://my-unique-bucket-2026/dev/photo.jpg' deleted.

$ poetry run s3-manager object file my-unique-bucket-2026 dev/photo.jpg -del
Object 's3://my-unique-bucket-2026/dev/photo.jpg' does not exist. Nothing to delete.

$ poetry run s3-manager bucket organize my-unique-bucket-2026 -ext
INFO: Moving s3://my-unique-bucket-2026/demo.csv to csv/demo.csv...
INFO: Moving s3://my-unique-bucket-2026/image.jpg to jpg/image.jpg...
INFO: Moving s3://my-unique-bucket-2026/users.csv to csv/users.csv...
csv - 2
jpg - 1

$ poetry run s3-manager host deploy beka-site-2026 static_web_page/index.html
INFO: Creating bucket 'beka-site-2026' in region us-east-1...
INFO: Bucket 'beka-site-2026' created successfully.
INFO: Disabling Block Public Access for 'beka-site-2026'...
INFO: Applying bucket policy to 'beka-site-2026'...
INFO: Bucket policy applied.
INFO: Turning on static website hosting for 'beka-site-2026'...
INFO: Static website hosting turned on.
INFO: Uploading static_web_page/index.html to s3://beka-site-2026/index.html (text/html; charset=utf-8)...
Uploaded 1 file(s) to 'beka-site-2026'.
Website: http://beka-site-2026.s3-website-us-east-1.amazonaws.com

$ poetry run python main.py host beka-demo-site-2026 --source static_web_page/html_demo_site
INFO: Creating bucket 'beka-demo-site-2026' in region us-east-1...
INFO: Bucket 'beka-demo-site-2026' created successfully.
INFO: Disabling Block Public Access for 'beka-demo-site-2026'...
INFO: Applying bucket policy to 'beka-demo-site-2026'...
INFO: Bucket policy applied.
INFO: Turning on static website hosting for 'beka-demo-site-2026'...
INFO: Static website hosting turned on.
INFO: Uploading static_web_page/html_demo_site/LICENSE to s3://beka-demo-site-2026/LICENSE (text/plain; charset=utf-8)...
INFO: Uploading static_web_page/html_demo_site/README.md to s3://beka-demo-site-2026/README.md (text/markdown; charset=utf-8)...
INFO: Uploading static_web_page/html_demo_site/about.html to s3://beka-demo-site-2026/about.html (text/html; charset=utf-8)...
INFO: Uploading static_web_page/html_demo_site/images/background.jpg to s3://beka-demo-site-2026/images/background.jpg (image/jpeg)...
INFO: Uploading static_web_page/html_demo_site/images/large-profile.jpg to s3://beka-demo-site-2026/images/large-profile.jpg (image/jpeg)...
INFO: Uploading static_web_page/html_demo_site/images/small-profile.jpeg to s3://beka-demo-site-2026/images/small-profile.jpeg (image/jpeg)...
INFO: Uploading static_web_page/html_demo_site/index.html to s3://beka-demo-site-2026/index.html (text/html; charset=utf-8)...
Uploaded 7 file(s) to 'beka-demo-site-2026'.
Website: http://beka-demo-site-2026.s3-website-us-east-1.amazonaws.com

$ poetry run s3-manager host deploy beka-react-2026 static_web_page/react-app
ERROR: No index.html in 'static_web_page/react-app'. React project? Run 'npm run build' first and pass the dist/ folder.

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
| `bucket create NAME [--enable-acl] [--region R] [-vers\|--versioning]` | Create a bucket in `AWS_REGION`, or in `--region`. `--enable-acl` allows object ACLs. `-vers` enables versioning right after creating it. |
| `bucket ensure NAME [--enable-acl]` | Create the bucket only if it does not exist, otherwise report that it exists |
| `bucket delete NAME [-y]` | Delete an empty bucket if it exists, otherwise report that it does not exist. Asks for confirmation unless `-y` is given. |
| `bucket exists NAME` | Check if a bucket exists (exit code `0` = yes, `1` = no) |
| `bucket versioning NAME [-vs\|--status] [--enable\|--suspend]` | `-vs`: show whether versioning is enabled, suspended or was never enabled. `--enable` / `--suspend` change it first. |
| `bucket encryption NAME [-es\|--status] [--enable]` | `--enable`: encrypt new objects by default with SSE-S3 (`AES256`). `-es`: show the default encryption algorithm. One of them is required. |
| `bucket organize NAME [-ext\|--by-extension] [--unknown]` | With `-ext`: move every file in the bucket root into a folder named after its extension (`image.jpg` → `jpg/image.jpg`) and print the number of moved files per extension (`csv - 2`). Files already in a folder are left alone, files without an extension too unless `--unknown` is given (then they go to `unknown/`). Without the flag: show a hint (exit code `1`). |
| `object upload PATH BUCKET [--key KEY] [--method auto\|small\|large\|upload_file\|upload_fileobj] [--threshold-mb 100] [--part-size-mb 10] [--validate-mime]` | Upload a local file. Key defaults to the file name. `auto` uses a multipart upload for files of at least `--threshold-mb`, otherwise one request. `upload_file` / `upload_fileobj` let boto3 manage the transfer. `--validate-mime` only accepts the media types listed below. |
| `object list BUCKET [--prefix P]` | List every object (or only those under `--prefix`) with size and last change date, plus the total |
| `object upload-url URL BUCKET [--key KEY]` | Download a file from a URL and upload it to S3. Only `.bmp`, `.jpg`, `.jpeg`, `.png`, `.webp`, `.mp4` are accepted. |
| `object set-acl BUCKET KEY [--acl public-read\|private]` | Set the ACL of one object |
| `object file BUCKET KEY [-del\|--delete \| -ver\|--versions \| -rp\|--restore-previous \| -rbt\|--rollback-to VERSION_ID]` | With `-del`: delete the object if it exists, otherwise report that it does not exist. With `-ver`: show the number of versions and when each was created. With `-rp`: upload the second-to-last version again as a new version (needs at least 2 versions). With `-rbt VERSION_ID`: the same for any version id shown by `-ver`. Without a flag: show its size and Content-Type (exit code `1` if it does not exist). Only one flag at a time. |
| `object old-versions BUCKET KEY [KEY...] [--months 6] [--dry-run] [-y]` | For each key: list its versions (old ones are marked), then **permanently** delete every version created more than `--months` months ago. The latest version is never deleted. Asks for confirmation unless `-y`; `--dry-run` deletes nothing. A missing key is reported and skipped (exit code `1` at the end). `--months 0` treats every non-latest version as old. |
| `policy generate-public-read BUCKET [--prefix P ...]` | Print a public-read policy (does not change anything). With `--prefix`, only those folders are public. |
| `policy create BUCKET` | Apply the public-read policy to the bucket |
| `policy get BUCKET` | Show the current bucket policy |
| `policy ensure BUCKET [--prefix P ...]` | If the bucket has no policy, turn off Block Public Access and make the given folders public (default: `dev/` and `test/`). If it has one, report it and change nothing. |
| `policy create-multiple BUCKET [-y]` | Turn off Block Public Access and apply the lecture's multiple policy: everyone can list versions, read objects, read/change object ACLs and **delete objects**. Asks for confirmation unless `-y`. Test buckets only. |
| `policy set-public BUCKET` | Turn off Block Public Access and apply the public-read policy |
| `lifecycle set BUCKET [--days 120] [--prefix P]` | Delete objects `--days` days after creation (whole bucket, or only under `--prefix`). Replaces existing lifecycle rules. |
| `lifecycle get BUCKET` | Show the current lifecycle rules |
| `lifecycle delete BUCKET [-y]` | Remove all lifecycle rules (asks for confirmation unless `-y`) |
| `host deploy BUCKET (PATH \| --source PATH) [--spa \| --error-document KEY] [--region R] [--open]` | Host a website in one step: create the bucket if it does not exist, turn off Block Public Access, apply a public-read policy for the whole bucket (**replaces** an existing bucket policy), turn on website hosting, upload, print the URL. PATH is an `index.html` or a folder with `index.html` at the top (a React app: its `dist/` folder), given as argument or with `--source` / `-s`. `--spa` also shows `index.html` for unknown paths. `--error-document error.html` shows that page instead (it must be in the folder, or next to the deployed file, which then uploads it too). `--open` opens the website in the browser. |
| `host BUCKET --source PATH [options]` | Shortcut for `host deploy`: a first word that is not a host subcommand is the bucket. Same options as `host deploy`. A bucket named like a subcommand (`status`, `delete`, ...) needs `host deploy`. |
| `host config BUCKET (--enable\|--disable) [--spa \| --error-document KEY] [--open]` | Only turn website hosting on (index document `index.html`) or off. Files and policy are not changed. With `--error-document` it warns if that key is not in the bucket yet. |
| `host upload BUCKET PATH [--prefix P]` | Only upload a website file or folder (subfolders kept) with the right Content-Type and Cache-Control |
| `host status BUCKET [--open]` | Show whether website hosting is on, its index and error page, and the website URL |
| `host delete BUCKET [--purge] [-y]` | Take a website down: turn website hosting off. With `--purge` also delete every object in the bucket. The bucket and its policy stay. Asks for confirmation unless `-y`. |
| `detect deploy BUCKET [--provider api-ninjas\|huggingface] [--function-name object-detection] [--role-arn ARN]` | Create (or update) the object detection Lambda function in the bucket's region with the API key from `.env`, allow S3 to call it, and call it for every new `.jpg` / `.jpeg` / `.png` in the bucket. |
| `detect image BUCKET FILE [--key K] [--timeout 60] [--min-confidence 0.5]` | Upload an image (type checked with python-magic), wait for the `.json` result the function writes, and print each object with its confidence and box |
| `detect local FILE [--provider ...] [--min-confidence 0.5]` | Send an image straight to the API, without AWS (to test the key) |
| `detect delete BUCKET [--function-name ...] [-y]` | Remove the image triggers from the bucket and delete the function. Images and results stay. |
| `quote [BUCKET] --inspire [AUTHOR] [--save] [--key KEY]` | `--inspire`: print a random quote; `--inspire "AUTHOR"`: a random quote by that author (full name or slug, exit code `1` if the API has none). `--save`: also save the quote as JSON in BUCKET, under `quotes/<author-slug>/<id>.json` or `--key` (`.json` is added if missing). BUCKET goes before `--inspire`. Without `--inspire`: show a hint (exit code `1`). |

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

Added for the lecture 3 assignment (small / large uploads, MIME validation, lifecycle policy):

| Function | Module | Used by command |
|---|---|---|
| `upload_file()` | `objects.py` | `object upload` (picks the method below) |
| `upload_small_file()` | `objects.py` | `object upload --method small` |
| `upload_large_file()` | `objects.py` | `object upload --method large` |
| `validate_media_type()` | `objects.py` | `object upload --validate-mime`, `object upload-url` |
| `create_expiration_rule()` | `lifecycle.py` | `lifecycle set` |
| `set_lifecycle_policy()` | `lifecycle.py` | `lifecycle set` |
| `read_lifecycle_policy()` | `lifecycle.py` | `lifecycle get` |
| `delete_lifecycle_policy()` | `lifecycle.py` | `lifecycle delete` |

Added for the "delete a file with a flag" assignment:

| Function | Module | Used by command |
|---|---|---|
| `get_object_info()` | `objects.py` | `object file` (checks the object exists) |
| `delete_object()` | `objects.py` | `object file -del` |

Added for the versioning assignment:

| Function | Module | Used by command |
|---|---|---|
| `get_bucket_versioning()` | `buckets.py` | `bucket versioning -vs` |
| `set_bucket_versioning()` | `buckets.py` | `bucket versioning --enable`, `--suspend` |
| `list_object_versions()` | `objects.py` | `object file -ver` |
| `restore_previous_version()` | `objects.py` | `object file -rp` |

Added for the "delete versions older than 6 months" assignment (the Catch UP 2 script has no
such feature):

| Function | Module | Used by command |
|---|---|---|
| `list_object_versions()` | `objects.py` | `object old-versions` (checks the versions of each file) |
| `months_ago()` | `objects.py` | `object old-versions --months` (the cutoff date) |
| `select_old_versions()` | `objects.py` | `object old-versions` (old versions except the latest) |
| `delete_object_version()` | `objects.py` | `object old-versions` (permanent delete) |

Added from the Catch UP 2 lecture script (lecture 4), which shows a whole argparse CLI. Its
features were ported into the Typer commands above instead of copying the script:

| Function | Module | Used by command | Catch UP 2 flag |
|---|---|---|---|
| `init_client(region)` | `client.py` | `bucket create --region` | `-region` |
| `set_bucket_versioning()` | `buckets.py` | `bucket create -vers` | `-cb -vers True` |
| `set_bucket_encryption()` | `buckets.py` | `bucket encryption --enable` | `-ben` |
| `read_bucket_encryption()` | `buckets.py` | `bucket encryption -es` | `-rben` |
| `get_objects()` | `objects.py` | `object list` | `-lo` |
| `upload_managed_file()` | `objects.py` | `object upload --method upload_file` | `-u_t upload_file` |
| `upload_file_object()` | `objects.py` | `object upload --method upload_fileobj` | `-u_t upload_fileobj` |
| `rollback_to_version()` | `objects.py` | `object file -rbt VERSION_ID` | `-r_b_t` |
| `organize_by_extension(include_unknown=True)` | `objects.py` | `bucket organize -ext --unknown` | `-o_b` |
| `generate_multiple_policy()` | `policies.py` | `policy create-multiple` | `-amp` |

Added for the static website assignment, based on the lecture 5 catch-up script
(`host_static/host_web_configuration.py` and `host_static/host_web_page_files.py`):

| Function | Module | Used by command | Lecture 5 flag |
|---|---|---|---|
| `set_bucket_website_policy()` | `website.py` | `host config --enable/--disable`, `host deploy` | `host --website_configuration True` |
| `static_web_page_file()` | `website.py` | `host upload`, `host deploy` | `host --host_static index.html` |
| `read_bucket_website()` | `website.py` | `host status` | – |
| `website_content_type()`, `cache_control()` | `website.py` | headers of every uploaded file | – |
| `website_url()`, `get_bucket_region()` | `website.py`, `buckets.py` | prints the website address | – |
| `host_static_website()` | `website.py` | `host deploy` (all steps at once) | – |

The lecture's `bucket --assign_read_policy` step is `create_bucket_policy()`, which already existed.

The lecture 5 script also comes in a version with CLI arguments (`s3staticwebpagewithargs`), which
adds a `host` subcommand to its argparse CLI. Every argument of it already has a Typer equivalent:

| Lecture 5 argument | What it does | s3-manager |
|---|---|---|
| `host BUCKET_NAME` | bucket of the website (required) | `host ... BUCKET` |
| `-wc True` / `--website_configuration True` | `put_bucket_website` with `index.html` | `host config BUCKET --enable` |
| `-wc False` | `delete_bucket_website` | `host config BUCKET --disable` |
| `-hs NAME` / `--host_static NAME` | upload `static_web_page/NAME` (a file or a folder) | `host upload BUCKET PATH` (any path) |

My own arguments, added for the "experiment by adding your own custom arguments" task:

| Argument | Function | What it does |
|---|---|---|
| `host deploy/config --error-document error.html` | `find_error_document()`, `set_bucket_website_policy(error_document=)` | custom 404 page; the lecture has `ErrorDocument` commented out |
| `host deploy/config/status --open` | `typer.launch()` | opens the website in the browser |
| `host delete [--purge]` | `delete_website()` | turns hosting off, with `--purge` deletes every object |

Added for the "host a webpage's source code with a single command" task
(`python main.py host "my-s3-static-host" --source "project_folder"`). The source is
[do-community/html_demo_site](https://github.com/do-community/html_demo_site) (MIT, its `LICENSE` is
kept), downloaded to `static_web_page/html_demo_site/`. `main.py` runs the same Typer CLI as
`s3-manager`, and `host BUCKET --source FOLDER` runs `host deploy`, which already did every step:

| Task step | Function | Module |
|---|---|---|
| upload the source code to the bucket (argument) | `ensure_bucket()`, `static_web_page_file()` | `buckets.py`, `website.py` |
| configure the bucket with `WebsiteConfiguration` | `set_bucket_website_policy()` (`put_bucket_website`) | `website.py` |
| set the permissions | `disable_block_public_access()`, `generate_public_read_policy()`, `create_bucket_policy()` | `policies.py` |
| return the URL of the static webpage | `website_url()`, `get_bucket_region()` | `website.py`, `buckets.py` |
| all of it in one command | `host_static_website()`, `DefaultDeployGroup` | `website.py`, `cli.py` |

Added for the lecture 5 Lambda example (`lambda_1.py`: S3 trigger → object detection API →
JSON next to the image). The function is in `aws_lambda/object_detection.py`; the CLI deploys it:

| Function | Module | Used by |
|---|---|---|
| `lambda_handler()` | `aws_lambda/object_detection.py` | Lambda, for every new image in the bucket |
| `query_image()` | `aws_lambda/object_detection.py` | picks the provider from `PROVIDER` |
| `query_api_ninjas()` | `aws_lambda/object_detection.py` | default provider (`API_NINJAS_KEY`) |
| `query_huggingface()` | `aws_lambda/object_detection.py` | `PROVIDER=huggingface` (`HF_API_TOKEN`) |
| `build_lambda_zip()`, `deploy_detection_lambda()` | `detection.py` | `detect deploy` (create or update the function) |
| `allow_s3_invoke()`, `set_detection_trigger()` | `detection.py` | `detect deploy` (the S3 trigger the lecture sets up in the console) |
| `wait_for_result()`, `normalize_detections()` | `detection.py` | `detect image` |
| `query_detection_api()` | `detection.py` | `detect local` |
| `remove_object_detection()` | `detection.py` | `detect delete` |

Added for the lecture 5 "inspire" script (`main.py` with `get_quote()` and `print_quote()`, turned
into the `quote` command with `--inspire` and `--save`):

| Function | Module | Used by |
|---|---|---|
| `get_quote(author=None)` | `quotes.py` | `quote --inspire [AUTHOR]` |
| `format_quote()` (the lecture's `print_quote()`, returns the text so the CLI prints it) | `quotes.py` | `quote --inspire` |
| `quote_key()`, `save_quote()` | `quotes.py` | `quote BUCKET --inspire --save` |

The script's `-u_t put_object` and `-u_t multipart_upload` were already here as `--method small`
and `--method large`, and `-l_v` as `object file -ver`.

Differences from the lecture code:

- Functions don't `print` or return `False` on errors. They log with `logging` and let the
  exception go up, and the CLI turns it into one readable error line.
- `download_file_and_upload_to_s3()` checks the file type with python-magic instead of always
  sending `image/jpg` (see [File type validation](#file-type-validation)).
- The lecture's `upload_file()` always used the key `hello.txt` and its return value is `None`
  (boto3's `upload_file` returns nothing), so checking `response["ResponseMetadata"]` crashes.
  Here the key comes from the file name or `--key`, and errors are raised as exceptions.
- Catch UP 2 bugs that are fixed here: `put_object(ExtraArgs=...)` fails (`put_object` takes
  `ContentType=` directly), `multiple_policy` uses `"Resource": "*"`, which a bucket policy rejects
  (here the bucket ARN and the `bucket/*` ARN), organizing lists the whole bucket and so moves
  already sorted files again (`csv/a.csv` → `csv/csv/a.csv`), an empty bucket or an object without
  versions crashes with `KeyError`, the multipart upload is never aborted on errors, and
  `LocationConstraint` is always sent, which fails in `us-east-1`.
- Lecture 5 static site bugs that are fixed here: only `.html` and `.css` files are uploaded, so a
  React build loses its `.js`, `.svg` and `.json` files without any message; python-magic says
  `text/plain` for CSS and JavaScript (here the extension decides); the URL always uses
  `s3-website-REGION`, which is wrong for newer regions like `eu-central-1`
  (`s3-website.eu-central-1`); the region comes from an env variable instead of the bucket; and there
  is no error document, so a React app can't handle unknown paths.
- Lecture 5 Lambda bugs that are fixed here: the object key from the S3 event is used as it is, but
  S3 URL-encodes it (`my photo.jpg` arrives as `my+photo.jpg`), so `download_fileobj` fails for
  names with spaces (here `unquote_plus`); every file that is not `.json` is sent to the API, also
  `.txt` or `.pdf` (here only images, and the trigger itself only fires for images, so the `.json`
  result can't call the function again); an API error (wrong key, 401) is saved as if it were a
  result (here `{"error", "status"}` and a log line); the JSON is uploaded without a Content-Type;
  and the code contains a placeholder Hugging Face token as default (here an empty default and a
  clear error).
- Lecture 5 inspire bugs that are fixed here: every error is caught and printed, then `{}` is
  returned, so `print_quote()` crashes with `KeyError` (here the error goes up and is shown as one
  line); the API answers an unknown author with `{}` and status 200, which also ends in that
  `KeyError` (here "No quotes found for author ..."); and `urlopen` has no timeout (here 10 seconds).
- The lecture fixes the permissions issue with `delete_public_access_block()`. This tool uses
  `put_public_access_block()` with all four settings set to `False`, which has the same effect but
  keeps an explicit configuration on the bucket. It is a separate step (`policy set-public`,
  `policy ensure`) rather than hidden inside `create_bucket_policy()`.

## Project structure

```text
s3-manager/
├── src/s3_manager/
│   ├── cli.py         # Typer commands, logging setup, error handling
│   ├── client.py      # init_client(region): reads .env and creates the boto3 client
│   ├── buckets.py     # list_buckets, create_bucket, delete_bucket, bucket_exists, get_bucket_region,
│   │                  # get_bucket_versioning, set_bucket_versioning,
│   │                  # set_bucket_encryption, read_bucket_encryption
│   ├── objects.py     # upload_file (small / large / upload_file / upload_fileobj),
│   │                  # download_file_and_upload_to_s3, set_object_access_policy, get_objects,
│   │                  # get_object_info, delete_object, list_object_versions, restore_previous_version,
│   │                  # rollback_to_version, months_ago, select_old_versions, delete_object_version,
│   │                  # extension_folder, move_object, organize_by_extension
│   ├── policies.py    # generate_public_read_policy, generate_multiple_policy, create_bucket_policy,
│   │                  # read_bucket_policy
│   ├── lifecycle.py   # set_lifecycle_policy, read_lifecycle_policy, delete_lifecycle_policy
│   ├── website.py     # set_bucket_website_policy, read_bucket_website, static_web_page_file,
│   │                  # website_content_type, cache_control, website_url, find_error_document,
│   │                  # host_static_website, delete_website
│   ├── detection.py   # build_lambda_zip, deploy_detection_lambda, allow_s3_invoke,
│   │                  # set_detection_trigger, wait_for_result, normalize_detections, ...
│   └── quotes.py      # get_quote, format_quote, quote_key, save_quote
├── aws_lambda/
│   └── object_detection.py  # the Lambda function: lambda_handler, query_image, query_api_ninjas,
│                            # query_huggingface (deployed on its own, so no s3_manager imports)
├── static_web_page/
│   ├── index.html     # the simple site: a page with my first and last name
│   ├── error.html     # custom 404 page for --error-document
│   ├── html_demo_site/  # source of github.com/do-community/html_demo_site (MIT) for main.py host
│   └── react-app/     # the React demo (Vite): npm install && npm run build -> dist/
├── main.py            # python main.py ... runs the same CLI as the s3-manager command
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
2. **`buckets.py`, `objects.py`, `policies.py`, `lifecycle.py`, `website.py`** contain plain functions that do the S3 work. They
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
  extension, or from the file content when the extension is unknown or `--validate-mime` is used)
  so that browsers show images instead of downloading them.
- **Folders don't really exist.** A key like `images/photo.jpg` is just a string; the AWS console
  only displays it as a folder. Keys also have no leading slash, so a policy for `/dev/*` would
  match nothing. `--prefix /dev`, `--prefix dev` and `--prefix dev/` all mean the `dev/` folder.
- **Deleting a missing key succeeds.** `delete_object` returns success even if the key does not
  exist, so `object file -del` first calls `head_object` (`get_object_info()`) to tell "deleted"
  apart from "nothing to delete". In a bucket with versioning turned on, `delete_object` only adds a
  delete marker and the old versions stay (and are still billed).
- **`-del` is one flag.** CLI options with a single dash are usually one letter (`-y`), but Click
  (which Typer uses) also accepts longer names, so `-del` is not read as `-d -e -l`. `--delete` works too.

### Small vs large uploads

`object upload` has two ways to send a file, implemented with the low-level S3 calls so each step
is visible:

| | Small (`upload_small_file`) | Large (`upload_large_file`) |
|---|---|---|
| S3 API | one `put_object` request | `create_multipart_upload` → `upload_part` × N → `complete_multipart_upload` |
| Memory | whole file at once | one part at a time (default 10 MiB) |
| Limits | max 5 GiB | parts of at least 5 MiB (except the last), max 10,000 parts, objects up to 5 TiB |
| On a network error | the whole file is sent again | the upload is aborted with `abort_multipart_upload` |

With `--method auto` (the default) files of at least 100 MiB use the multipart upload, which is
where AWS recommends switching. `--threshold-mb` and `--part-size-mb` change these numbers.

Each `upload_part` call returns an `ETag` (a checksum of the part). `complete_multipart_upload` gets
the list of part numbers and ETags, and only then does S3 join the parts into one object. Until
then the parts are invisible in the bucket **but still stored and billed**. That's why any error
during the upload calls `abort_multipart_upload`, which deletes the parts that were already sent.

`--method upload_file` and `--method upload_fileobj` use boto3's **managed transfer** instead.
`upload_file` takes the file path and `upload_fileobj` an open file (or any file-like object, like the
`io.BytesIO` in `download_file_and_upload_to_s3`). boto3 then decides by itself: one request for
small files, a multipart upload from 8 MiB, with several parts sent in parallel threads and failed
parts retried. Less control and less to see, but less code. The Content-Type goes in
`ExtraArgs={"ContentType": ...}`, which only these managed calls accept (`put_object` takes
`ContentType=` directly).

### Default encryption

`bucket encryption NAME --enable` calls `put_bucket_encryption` with the rule
`{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}`: S3 encrypts every new object
with keys it manages (SSE-S3) when it stores it, and decrypts it when it is read, so downloads work
as before. Objects that are already in the bucket are not re-encrypted. Since January 2023 AWS does
this for every new bucket anyway, so `-es` usually shows `AES256` even without `--enable`; it shows
`aws:kms` for buckets that use KMS keys, and "no default encryption" if AWS answers with
`ServerSideEncryptionConfigurationNotFoundError` (e.g. some S3-compatible services).

### Lifecycle policy

A lifecycle configuration is a list of rules that S3 runs on its own, without any request from us.
`lifecycle set` creates this rule:

```json
{
  "ID": "expire-all-after-120-days",
  "Status": "Enabled",
  "Filter": {"Prefix": ""},
  "Expiration": {"Days": 120}
}
```

`"Expiration": {"Days": 120}` means every object is deleted 120 days after it was created
(uploaded). An empty prefix matches every object; `--prefix logs/` would only match keys starting
with `logs/`. A few details:

- S3 rounds the time up to the next midnight UTC and deletes expired objects in the background, so
  an object can stay visible a little longer, but you are not billed for it after it expired.
- `put_bucket_lifecycle_configuration` **replaces** the whole configuration, so setting a rule
  removes any other rules the bucket had.
- The rule also applies to objects that were uploaded before it was created: an object that is
  already older than 120 days is deleted soon after the rule is set.

### Versioning

With versioning enabled, S3 keeps every version of an object instead of overwriting it. Each
upload to the same key creates a new version with its own `VersionId`, and a normal download
returns the newest one (`IsLatest`).

- **Status.** `get_bucket_versioning` returns `Enabled` or `Suspended`. A bucket where versioning was
  never turned on has no `Status` at all, so the command reports "never been enabled". Versioning
  can't be turned off again once it was enabled, only suspended (new uploads overwrite, old
  versions stay).
- **Listing versions.** `list_object_versions` takes a `Prefix`, not an exact key, so `photo.jpg`
  would also return versions of `photo.jpg.bak`. `list_object_versions()` keeps only the exact key,
  goes through all pages with a paginator, and sorts the versions newest first. A version can't be
  changed after it is written, so its `LastModified` date is its creation date. Delete markers
  (created by deleting an object in a versioned bucket) have no content, so they are not counted.
  In a bucket without versioning an object has one version with the id `null`.
- **Uploading the previous version again.** `-rp` takes the second-to-last version and calls
  `copy_object` with `CopySource={"Bucket", "Key", "VersionId"}` onto the same key. S3 copies it on
  the server side (nothing is downloaded), keeps its Content-Type, and stores it as a new latest
  version, so all older versions stay and the step can be undone. `copy_object` works for objects
  up to 5 GiB. With fewer than 2 versions the command stops with an error.
- **Going back to any version.** `-rbt VERSION_ID` does the same `copy_object` for a version id
  chosen from the `-ver` list. `rollback_to_version()` first checks that the id belongs to this key
  and is not already the latest version, so a typo gives a clear error instead of an AWS one.
- **Deleting old versions.** `object old-versions` lists the versions of every key it gets with
  `list_object_versions()`, picks the ones created before the cutoff with `select_old_versions()`
  and deletes each with `delete_object(Bucket, Key, VersionId=...)`. With a `VersionId` the version
  is removed **for good**: no delete marker is added and it can't be restored, which is why the
  command asks first and has `--dry-run`. This needs the `s3:DeleteObjectVersion` permission.
  - The cutoff is "6 calendar months ago" from `months_ago()` (2026-10-19 → 2026-04-19; if the
    day doesn't exist, the last day of the month: 08-31 → 02-28), using only the standard library.
  - The latest version is never deleted, even if it is older than 6 months, so the file itself
    stays and only its old history goes. In a bucket without versioning each object has one `null`
    version, which is always the latest, so nothing is deleted there.
  - Delete markers are not versions with content (see above), so they are left alone.
  - A lifecycle rule with `NoncurrentVersionExpiration` could do the same automatically, but it
    counts days from when a version *stopped* being the latest, not from its creation date.

### Organizing files by extension

`bucket organize NAME -ext` calls `organize_by_extension()`, which moves `image.jpg` to
`jpg/image.jpg`, `demo.csv` to `csv/demo.csv` and so on, and returns a `Counter` with the number of
moved files per extension. The CLI prints it sorted by extension, one line each (`csv - 4`).

- **S3 has no folders and no "move".** A folder is only a key prefix (`csv/`), so the console shows
  `csv/` as soon as one key starts with it. A move is two requests: `copy_object` to the new key
  (done inside S3, nothing is downloaded, the Content-Type is kept, works up to 5 GiB) and then
  `delete_object` on the old key.
- **Only the bucket root.** `list_objects_v2` is called with `Delimiter="/"`, so S3 returns only
  root-level keys in `Contents` and groups everything inside folders under `CommonPrefixes`. Files
  that are already in a folder are not touched, so running the command again changes nothing.
  All keys are listed first (with a paginator, so more than 1000 objects work) and moved afterwards.
- **Folder name.** `extension_folder()` takes the last extension in lowercase without the dot
  (`Photo.JPG` → `jpg`, `archive.tar.gz` → `gz`). Files without an extension (`README`, `.env`) are
  skipped and not counted, unless `--unknown` is given: then they are moved to `unknown/` and
  counted as `unknown - N` (like the Catch UP 2 script does).
- If the target key already exists it is overwritten. In a versioned bucket the old key only gets a
  delete marker, so its versions are still there.

### File type validation

`download_file_and_upload_to_s3()` always, and `object upload --validate-mime` on request, only
accept `.bmp`, `.jpg`, `.jpeg`, `.png`, `.webp` and `.mp4` (the check is in `validate_media_type()`).
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

`policy create-multiple` applies the lecture's "multiple" policy. It has two statements, because
bucket actions and object actions need different resources: `s3:ListBucketVersions` on
`arn:aws:s3:::BUCKET`, and `s3:GetObject`, `s3:GetObjectAcl`, `s3:PutObjectAcl`, `s3:DeleteObject`
on `arn:aws:s3:::BUCKET/*`. With `"Principal": "*"` this means anyone on the internet can delete the
files, so the command asks for confirmation and is only meant for test buckets.

### Static website hosting

S3 can serve a bucket as a website. Turning it on (`put_bucket_website`) gives the bucket a second
address, the **website endpoint**:

```text
http://BUCKET.s3-website-us-east-1.amazonaws.com     older regions use a dash
http://BUCKET.s3-website.eu-central-1.amazonaws.com  newer regions use a dot
```

The normal (REST) address `https://BUCKET.s3.amazonaws.com/index.html` only returns single files.
The website endpoint returns the **index document** (`index.html`) for `/` and for every folder,
and an optional **error document** for keys that do not exist. It only supports HTTP; for HTTPS
and a custom domain, CloudFront is put in front of the bucket.

The website endpoint can't send credentials, so the files must be public. `host deploy` therefore
does the same steps as the lecture, in this order:

1. create the bucket if it is missing (`ensure_bucket`)
2. turn off Block Public Access (`disable_block_public_access`)
3. apply the public-read policy for the whole bucket (`create_bucket_policy`); this replaces an
   existing bucket policy, so don't deploy a website to a bucket with private files
4. turn on website hosting (`set_bucket_website_policy`)
5. upload the files (`static_web_page_file`) and print the URL for the bucket's real region
   (`get_bucket_location`; AWS answers `None` for us-east-1)

The path is checked first, so nothing is changed in AWS if there is no `index.html`.

**Content-Type.** The browser decides what to do with a file by its `Content-Type`. An HTML page
sent as `binary/octet-stream` is downloaded instead of shown, and a JavaScript module sent as
`text/plain` is refused, so a React app stays blank. For web files the extension is more reliable
than python-magic, which only sees "text" in CSS and JS. `website_content_type()` uses the
extension (`mimetypes` plus a small table for `.js`, `.css`, `.svg`, `.woff2`, ...) and only asks
python-magic for unknown extensions. Text types get `; charset=utf-8`, so Georgian text displays
correctly.

**Cache-Control.** HTML files are sent with `no-cache` (the browser checks for a new version every
time), so a new deploy shows up right away. Other files are cached for a day; Vite puts a hash in
their names (`index-a1b2c3.js`), so a new build gets new names anyway.

**React.** A React app is plain files after `npm run build`: an `index.html`, and JS / CSS in
`assets/`. The CLI uploads the `dist/` folder with its subfolders (`collect_site_files` keeps the
paths, skips hidden files like `.DS_Store`). `vite.config.js` uses `base: "./"`, so the asset paths
are relative and the app also works under a folder (`host upload --prefix`). With `--spa` the error
document is `index.html` too, so a path like `/about` loads the app instead of S3's 404 page (S3
still answers with status 404 then; CloudFront can turn that into 200).

**My own arguments.** `--error-document error.html` sets S3's `ErrorDocument`, so a missing page
shows my own 404 page instead of S3's XML error. When a single `index.html` is deployed, the error
page next to it is uploaded too (an error page in a subfolder, like `pages/404.html`, keeps that key).
The file is checked before anything is changed in AWS. `--spa` and `--error-document` can't be used
together, because both set the same `ErrorDocument`. `--open` uses `typer.launch()`, which opens the
URL with the system's default browser. `host delete` is the opposite of `host deploy`: it calls
`delete_bucket_website`, and with `--purge` deletes every object. It does not remove the public-read
policy or the bucket, so without `--purge` the files are still public through the normal S3 URL.

**One command: `python main.py host BUCKET --source FOLDER`.** `main.py` adds `src/` to the import
path and calls the same `main()` as the `s3-manager` command (the dependencies still come from the
Poetry environment, so run it with `poetry run python main.py`). `host` is a Typer group, so
normally its first word must be a subcommand. The group uses its own Click class,
`DefaultDeployGroup`: when the first word is not a subcommand (and not an option like `--help`),
it puts `deploy` in front, so `host my-bucket --source site/` becomes
`host deploy my-bucket --source site/`. `host config/upload/status/delete` work as before; only a
bucket named exactly like a subcommand needs `host deploy` written out. `--source` / `-s` is the
same as the PATH argument; passing both or neither stops before any AWS call.

### Object detection with AWS Lambda

```text
detect image ──upload cat.jpg──> S3 bucket ──"ObjectCreated" event──> Lambda: object_detection.py
                                    ^                                        │ download cat.jpg
                                    │                                        │ POST to API Ninjas
detect image <──reads cat.json──────┴──────────── put_object cat.json <──────┘
```

**The function** (`aws_lambda/object_detection.py`) is the lecture's `lambda_1.py` with the bugs
above fixed. It receives an S3 event with one record per new object, downloads the image into
memory (`io.BytesIO`), sends it to the API and saves the answer as `<name>.json` in the same bucket.
It only uses the standard library, boto3 and urllib3, which the Lambda Python runtime already has,
so the deployment package is one file and no layer is needed. The S3 client and the HTTP pool are
created outside the handler, so Lambda reuses them while the container is warm.

**Providers.** `PROVIDER=api-ninjas` (default) sends the image as a form upload with the
`X-Api-Key` header. Hugging Face needs a bank card now even for the free tier, so it's only an
option (`--provider huggingface`, `HF_API_TOKEN`). Both answers are turned into one format by
`normalize_detections()`: API Ninjas sends the confidence as a string and the box as `x1..y2`,
Hugging Face sends `score` and `xmin..ymax`.

**`detect deploy`** does in code what the lecture does in the console:

1. zip the handler file (`build_lambda_zip`, in memory, the file at the top of the zip)
2. create the function (`create_function`, runtime `python3.13`, handler
   `object_detection.lambda_handler`, timeout 30 s), or update its code and settings if it exists.
   It waits with the `function_active_v2` / `function_updated_v2` waiters, because Lambda refuses
   changes while the previous one is still running.
   The function is created in the **bucket's region**, because S3 can only call functions there.
3. the role: AWS Academy accounts can't create IAM roles, but each has `LabRole`, so its ARN is
   built from the account id (`sts.get_caller_identity`). Use `--role-arn` or `LAMBDA_ROLE_ARN`
   for another role (it needs S3 read/write and CloudWatch Logs permissions).
4. `add_permission`: allow `s3.amazonaws.com` to invoke the function, only for this bucket
   (`SourceArn`) and this account (`SourceAccount`). This must come before the trigger, because S3
   tests the permission when the trigger is saved.
5. the trigger (`put_bucket_notification_configuration`): one `s3:ObjectCreated:*` rule per
   extension (`.jpg`, `.jpeg`, `.png` and upper case, because a filter allows one suffix and is
   case-sensitive). The function writes `.json` into the same bucket, and a trigger on every
   object would call it again for its own result, forever. The suffix filter prevents that.
   This call replaces all notifications of a bucket, so the existing ones are read first and kept.

The API key is copied from `.env` into the function's environment variables. Anyone who can open the
function in the Lambda console can see it, which is fine for a free key in a lab account; for real
projects it would go into Secrets Manager.

**`detect image`** deletes an old `cat.json` first (otherwise the old result would be read at
once), uploads the image with `put_object` (type checked with python-magic, so a renamed file is
rejected before it reaches Lambda), then polls `head_object` every 2 seconds until `cat.json`
exists, and prints the objects above `--min-confidence`. If no result appears within `--timeout`,
the function's logs are in CloudWatch (Lambda console → the function → *Monitor* → *View
CloudWatch logs*).

### Inspirational quotes

`quotes.py` calls `https://api.quotable.kurokeita.dev/api/quotes/random` with `requests` (with the
same browser user-agent as the lecture script and a 10 second timeout). With an author it adds
`?author=...`, which accepts the full name (`Nelson Mandela`) or the slug (`nelson-mandela`). The
answer looks like this:

```json
{"quote": {"id": "WAn2olKpNv", "content": "...", "tags": [...],
           "author": {"name": "Nelson Mandela", "slug": "nelson-mandela", "bio": "...", ...}}}
```

The command prints it like the lecture script (text, `---`, author). For an author the API doesn't
know, it still answers `200 OK`, but with `{}`, so `get_quote()` raises "No quotes found for author".
The assignment's example author **Linus Torvalds is not in the API's database** (checked on
2026-10-28: `{}` for the name, `422 Author with slug linus-torvalds not found` for the slug), so
`quote --inspire "Linus Torvalds"` prints that error; any author from the API works.

`--inspire` is an option with an optional value: alone it means "any author", with a value it is
the author. Typer options always need a value (Click's `is_flag=False, flag_value=""` trick is
ignored by Typer 0.27), so `OptionalInspireCommand.parse_args` in `cli.py` gives a bare
`--inspire` (last word, or followed by another option like `--save`) an empty value before Typer
parses the arguments, like the `host BUCKET` shortcut does. That's why BUCKET must come before it, otherwise
`--inspire my-bucket` would read the bucket name as the author. `--save` checks the bucket first,
then saves the whole `quote` object (text, author, tags) with `put_object` as pretty-printed JSON
with `Content-Type: application/json`. Without `--save` no AWS call is made, so plain `--inspire`
works without credentials.

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
- `requests` errors: a download or an API call (quotes) failed.
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
- small uploads sent with one `put_object`, and `auto` switching to multipart at the threshold
- multipart uploads: the file split into the right parts, parts completed in order with their
  ETags, the upload aborted when a part fails, and parts under 5 MiB rejected
- file type validation for URL downloads and `--validate-mime` uploads: allowed types accepted,
  missing extension added, unsupported type and wrong extension rejected before uploading
- the lifecycle rule (120 days by default, prefix without leading slash), and `lifecycle get`
  returning "no policy" for `NoSuchLifecycleConfiguration`
- rejecting an unknown ACL before calling AWS
- `get_object_info` returning size and type, `None` for 404 and raising for 403, and
  `delete_object` sending the right bucket and key
- `object file`: `-del` and `--delete` deleting an existing object, reporting a missing object or
  bucket without deleting anything, and only showing info when the flag is not given
- versioning: reading `Enabled` / `Suspended` / never enabled, enabling and suspending, listing
  versions (other keys and delete markers skipped, newest first, across pages), copying the
  second-to-last version with its `VersionId`, and rejecting `-rp` with fewer than 2 versions
- `bucket versioning` and `object file -ver` / `-rp` output, and rejecting two `object file` flags at once
- organizing by extension: the folder name for each key (lowercase, last extension, `None` for keys
  in a folder or without an extension), listing only the root with `Delimiter="/"`, a `copy_object`
  plus `delete_object` for every moved file, the count per extension, and skipping files without
  an extension
- `bucket organize`: the `csv - 2` / `jpg - 1` output, requiring `-ext`, reporting a missing bucket,
  and "Nothing to organize" when no file can be moved
- Catch UP 2 features: `bucket create --region ... -vers` (client created for that region, then
  versioning enabled), `put_bucket_encryption` with AES256 and reading the algorithm (or `None`),
  `bucket encryption` requiring a flag, `--method upload_file|upload_fileobj` calling boto3's
  managed uploads with the Content-Type, `get_objects` across pages with the prefix, `object list`
  output and empty bucket, `-rbt` copying the chosen version and rejecting unknown or latest ids,
  `--unknown` moving files without an extension, and the multiple policy (bucket and object ARNs,
  confirmation before applying)
- static website hosting: the website configuration with and without an error document, turning it
  off, reading it (or `None`), the website URL with a dash or a dot by region, the Content-Type
  per extension (CSS / JS not `text/plain`, charset for text) and from the content for unknown
  extensions, `no-cache` for HTML, folder uploads keeping subfolders and skipping `.DS_Store`, the
  `--prefix`, a missing bucket, the order of the `host deploy` steps, creating a missing bucket,
  no AWS call at all when `index.html` is missing (with the `npm run build` hint for a React
  project), the bucket region (`None` → us-east-1, `EU` → eu-west-1), and the `host` command output
- my own host arguments: the error page found in the folder or next to the file, missing error page
  and `--spa` with `--error-document` rejected before any AWS call, the error page uploaded with its
  key (also in a subfolder), `--open` calling `typer.launch` with the URL (patched), the warning
  when the error page is not in the bucket, and `host delete` with and without `--purge` (every page
  of objects deleted, nothing deleted when the confirmation is declined)
- the one-command host task: `host BUCKET --source FOLDER` running every deploy step (public access,
  policy, `WebsiteConfiguration`, upload, URL), `host deploy -s`, PATH and `--source` both or neither
  rejected before any AWS call, `host status` still not taken as a bucket name, and `python main.py`
  running the CLI (subprocess)
- the Lambda function (loaded from its file, S3 client and HTTP pool replaced by mocks): the
  URL-encoded key decoded, the result saved as `.json` with `application/json`, non-images
  skipped, the API Ninjas request (header, form field), a missing key or an API error saved as
  `{"error"}` without crashing, and the Hugging Face provider
- `detection.py`: the zip contains only the handler, the `LabRole` ARN, create vs update of the
  function (with the right waiter), the permission limited to the bucket and account (and an
  existing one ignored), other bucket notifications kept and old triggers replaced, the permission
  added before the trigger, polling and timeout for the result (fake clock), an old result deleted
  before uploading, `.gif` rejected, both API answer formats, and the `detect` command output
  (missing key in `.env`, region of the bucket, role from `.env`)
- quotes (`requests.get` patched): no `author` parameter for a random quote, the author sent as
  `params`, `{}` from the API turned into "No quotes found for author 'Linus Torvalds'", the
  lecture's output format, the default key `quotes/<slug>/<id>.json`, the JSON body and
  Content-Type sent with `put_object`, and `.json` added to a custom key
- `quote` command: `--inspire` with and without an author (no AWS call without `--save`),
  `--save` with the default and a custom key, and requiring `--inspire`, a bucket for `--save`
  and an existing bucket (checked before calling the API)

I also tested all commands manually against a real AWS account.

## Possible future improvements

- `bucket delete --force` to delete all objects before deleting the bucket
- deleting several keys at once with `delete_objects`
- streaming large downloads directly to S3 instead of keeping them in memory
- uploading a custom policy from a JSON file
- a `--dry-run` option that shows what would happen without changing anything
- integration tests with [moto](https://github.com/getmoto/moto) or a local MinIO container
- a progress bar for multipart uploads, and uploading the parts in parallel
- resuming an interrupted multipart upload instead of starting again
- adding a lifecycle rule without replacing the existing ones
- `host deploy --delete-removed` to delete files that are not in the new build any more
- CloudFront in front of the website bucket for HTTPS and a custom domain
- drawing the detected boxes onto the image (Pillow) and saving it next to the JSON
- the API key in AWS Secrets Manager instead of a Lambda environment variable

## Troubleshooting

**The website shows `403 Forbidden`.**
The files are not public. Block Public Access may be on at the account level (S3 console →
*Block Public Access settings for this account*), which `host deploy` can't change for the bucket
alone, or the bucket policy was replaced afterwards. `poetry run s3-manager policy get BUCKET` shows
the current policy.

**The website shows `404 Not Found` with `NoSuchWebsiteConfiguration`.**
Website hosting is off. Run `host config BUCKET --enable` or `host deploy` again. Also check that
you use the website URL (`s3-website`) printed by the command, not the normal bucket URL.

**The React page is blank or the CLI says `No index.html ... Run 'npm run build' first`.**
Upload the build output, not the source: `npm run build` in `static_web_page/react-app/`, then
deploy `static_web_page/react-app/dist`. If the page is still blank, check the browser's DevTools
→ Network tab: `.js` files must have `Content-Type: text/javascript`.

**`detect image` says `No result ... after 60 seconds`.**
Open the function's logs: Lambda console → `object-detection` → *Monitor* → *View CloudWatch logs*.
A `.json` with `"error": "Invalid API Key."` means the key in `.env` is wrong (fix it and run
`detect deploy` again; `detect local` tests it without AWS). No log at all means the trigger did not
fire: check S3 console → the bucket → *Properties* → *Event notifications*.

**`detect deploy` fails with `InvalidParameterValueException` or `AccessDeniedException`.**
The role can't be used. In AWS Academy use the default (`LabRole`); otherwise pass `--role-arn` with
a role that Lambda can assume. A role created a few seconds ago may need a moment before Lambda
accepts it.

**`https://` does not work for the website.**
The S3 website endpoint is HTTP only. Use `http://`, or put CloudFront in front of it.

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
