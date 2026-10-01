"""Build the single-file release (a Python zipapp) and find the one we are running from."""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import zipapp
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def running_archive() -> Path | None:
    """The .pyz this code was loaded from, or None when running from a source checkout."""
    for parent in Path(__file__).resolve().parents:
        if parent.is_file():
            return parent if zipfile.is_zipfile(parent) else None
    return None


def build(target: Path, version: str | None = None) -> Path:
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging) / "remote_code_bridge"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if version:
            init = package / "__init__.py"
            text = re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', init.read_text())
            init.write_text(text)
        target.parent.mkdir(parents=True, exist_ok=True)
        zipapp.create_archive(
            staging, target, interpreter="/usr/bin/env python3", main="remote_code_bridge.cli:main", compressed=True
        )
    return target


def archive_bytes() -> bytes:
    """The release file to install: the running one, or a fresh build from this checkout."""
    archive = running_archive()
    if archive is not None:
        return archive.read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        return build(Path(directory) / "remote-code-bridge.pyz").read_bytes()


if __name__ == "__main__":  # python -m remote_code_bridge.bundle OUTPUT [VERSION]
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: python -m remote_code_bridge.bundle OUTPUT [VERSION]")
    print(build(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) == 3 else None))
