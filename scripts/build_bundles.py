#!/usr/bin/env python3
"""Build ZIP-style Lambda bundles without Docker.

For each Lambda project (lambda/, engines/) this script:
  1. exports the locked, non-dev requirements (uv export --frozen),
  2. installs them into a staging directory (uv pip install --target),
  3. copies the project's own package into the staging root,
  4. leaves the directory for CDK (Code.from_asset) to package & upload.

The staging root mirrors the old container layout so handler paths stay
identical, e.g. engines/gemini/sns_handler or lambda/chatbot/telegram_api_handler.

Usage:
    python scripts/build_bundles.py            # run from the repo root
Requires: uv, and a Python 3.14 interpreter (the project venv).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ("lambda", "engines")


def python_for_uv() -> str:
    """uv pip install --target still wants an interpreter; use the active venv."""
    venv = Path(sys.prefix)
    if (venv / "Scripts" / "python.exe").exists():
        return str(venv / "Scripts" / "python.exe")
    if (venv / "bin" / "python").exists():
        return str(venv / "bin" / "python")
    return sys.executable


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True)


def main() -> None:
    if shutil.which("uv") is None:
        sys.exit("uv is required on PATH (see README -> Local development)")
    out_root = ROOT / "build" / "bundles"
    req_root = ROOT / "build"
    req_root.mkdir(parents=True, exist_ok=True)
    py = python_for_uv()

    for project in PROJECTS:
        project_dir = ROOT / project
        staging = out_root / project
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        reqs = req_root / f"{project}-requirements.txt"
        run("uv", "export", "--frozen", "--no-dev", "--format=requirements-txt",
            "-o", str(reqs), cwd=project_dir)
        try:
            run("uv", "pip", "install", "--python", py, "--target", str(staging),
                "-r", str(reqs), cwd=ROOT)
        finally:
            reqs.unlink(missing_ok=True)

        shutil.copytree(
            project_dir,
            staging / project,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc", ".pytest_cache"),
        )
        print(f"[build_bundles] {project} bundle ready at {staging}")


if __name__ == "__main__":
    main()
