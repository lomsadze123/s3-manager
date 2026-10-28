"""Run the CLI as a script: python main.py host BUCKET --source FOLDER (same as s3-manager ...)."""

import sys
from pathlib import Path

# Works without 'poetry install' of the package itself; the dependencies still come from the venv.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from s3_manager.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
