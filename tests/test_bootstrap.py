"""install.sh / install.ps1: find Python, download + verify the archive, run its installer."""

import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import zipapp

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A stand-in release archive that reports what it was asked to do.
STUB = 'import json, os, sys\nprint(json.dumps({"argv": sys.argv[1:], "gh": "GH_TOKEN" in os.environ}))\n'


@pytest.fixture
def release(tmp_path):
    source = tmp_path / "stub"
    source.mkdir()
    (source / "__main__.py").write_text(STUB)
    directory = tmp_path / "release"
    directory.mkdir()
    archive = directory / "remote-code-bridge.pyz"
    zipapp.create_archive(source, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / "remote-code-bridge.pyz.sha256").write_text(f"{digest}  remote-code-bridge.pyz\n")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args: None  # type: ignore[attr-defined]
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield directory, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def run_sh(url, *args):
    env = dict(os.environ, RCB_RELEASE_URL=url, GH_TOKEN="ghp_secret")
    return subprocess.run(["sh", os.path.join(ROOT, "install.sh"), *args], env=env, capture_output=True, text=True)


needs_sh = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("curl"), reason="needs sh and curl")


@needs_sh
def test_install_sh_runs_the_verified_archive(release):
    _, url = release
    result = run_sh(url, "devbox")
    assert result.returncode == 0, result.stderr
    reported = json.loads(result.stdout)
    assert reported == {"argv": ["install", "devbox"], "gh": False}


@needs_sh
def test_install_sh_rejects_a_bad_checksum(release):
    directory, url = release
    (directory / "remote-code-bridge.pyz.sha256").write_text("0" * 64 + "\n")
    result = run_sh(url)
    assert result.returncode != 0 and "checksum failed" in result.stderr


@needs_sh
def test_install_sh_usage_and_missing_release(release):
    _, url = release
    assert "usage" in run_sh(url, "a", "b").stderr
    assert "download failed" in run_sh(url + "/nope").stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_install_ps1_runs_the_verified_archive(release):
    _, url = release
    env = dict(os.environ, RCB_RELEASE_URL=url, GH_TOKEN="ghp_secret")
    script = os.path.join(ROOT, "install.ps1")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-SshAlias", "devbox"],
        env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr + result.stdout
    reported = json.loads(result.stdout.strip().splitlines()[-1])
    assert reported == {"argv": ["install", "devbox"], "gh": False}
