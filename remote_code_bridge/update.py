"""`remote-code-bridge update`: download the latest release, verify it, and run its installer."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from remote_code_bridge import BridgeError, is_valid_alias, ssh
from remote_code_bridge.config import host_config_path, read_host_config

RELEASE_BASE = "https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download"
ARCHIVE = "remote-code-bridge.pyz"


def download(url: str) -> bytes:
    """Anonymous first; on a GitHub rate limit, retry once with $GH_TOKEN if it is set."""
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        token = os.environ.get("GH_TOKEN", "")
        if error.code not in (403, 429) or not token or any(ord(char) < 33 for char in token):
            hint = " If GitHub rate-limited you, export GH_TOKEN and retry." if error.code in (403, 429) else ""
            raise BridgeError(f"download failed for {url}: HTTP {error.code}.{hint}") from error
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.URLError as retry_error:
            raise BridgeError(f"download failed for {url} even with GH_TOKEN: {retry_error}") from retry_error
    except urllib.error.URLError as error:
        raise BridgeError(f"download failed for {url}: {error.reason}") from error


def verified_release(base: str) -> bytes:
    archive = download(f"{base}/{ARCHIVE}")
    expected = download(f"{base}/{ARCHIVE}.sha256").decode("ascii", "replace").split()
    if not expected or hashlib.sha256(archive).hexdigest() != expected[0].lower():
        raise BridgeError(f"checksum failed for {ARCHIVE}")
    return archive


def update(alias: str | None = None) -> int:
    alias = alias or read_host_config(host_config_path()).default_host
    if not alias:
        raise BridgeError("could not determine SSH alias; run `remote-code-bridge update <ssh-alias>`")
    if not is_valid_alias(alias):
        raise BridgeError("SSH alias contains unsupported characters")
    base = (os.environ.get("RCB_RELEASE_URL") or RELEASE_BASE).rstrip("/")
    archive = verified_release(base)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ARCHIVE
        path.write_bytes(archive)
        result = subprocess.run([sys.executable, str(path), "install", alias, "--yes"], env=ssh.child_env())
    if result.returncode == 0:
        print(f"remote-code-bridge update: updated for SSH alias {alias}")
    return result.returncode
