import os
import sys
import tempfile
from pathlib import Path

import pytest

from remote_code_bridge import remote_setup

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the remote is Linux")


@pytest.fixture
def short_home(monkeypatch):
    home = Path(tempfile.mkdtemp(prefix="rcb", dir="/tmp"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    yield home
    import shutil

    shutil.rmtree(home, ignore_errors=True)


def test_code_is_ours(tmp_path):
    code = tmp_path / "code"
    assert remote_setup.code_is_ours(code)
    code.symlink_to("remote-code-bridge")
    assert remote_setup.code_is_ours(code)
    code.unlink()
    code.symlink_to("/usr/bin/real-code")
    assert not remote_setup.code_is_ours(code)
    code.unlink()
    code.write_text("#!/bin/sh\n# Remote-side wrapper for remote-code-bridge.\n")
    assert remote_setup.code_is_ours(code)


def test_socket_prefers_home_cache(short_home):
    assert remote_setup.choose_socket(short_home) == short_home / ".cache/remote-code-bridge/bridge.sock"


def test_socket_falls_back_when_home_path_is_too_long(tmp_path, monkeypatch):
    long_home = tmp_path / ("x" * 90)
    runtime = Path(tempfile.mkdtemp(prefix="rt", dir="/tmp"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    assert remote_setup.choose_socket(long_home) == runtime / "remote-code-bridge" / "bridge.sock"
    assert oct((runtime / "remote-code-bridge").stat().st_mode & 0o777) == "0o700"


def test_main_installs_and_reports_the_socket(short_home, capsys):
    bin_dir = short_home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    archive = bin_dir / ".remote-code-bridge.tmp"
    archive.write_bytes(b"#!/usr/bin/env python3\n")
    payload = {"env": {"REMOTE_CODE_BRIDGE_HOST_ALIAS": "devbox"}, "rc": ".profile", "path_line": "export X=1"}
    assert remote_setup.main(payload, str(archive)) == 0
    assert os.readlink(bin_dir / "code") == "remote-code-bridge"
    assert "export X=1" in (short_home / ".profile").read_text()
    assert '"socket"' in capsys.readouterr().out


def test_main_refuses_unrelated_code(short_home, capsys):
    bin_dir = short_home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "code").write_text("real vscode")
    archive = bin_dir / ".tmp"
    archive.write_bytes(b"x")
    assert remote_setup.main({}, str(archive)) == remote_setup.EXIT_UNRELATED_CODE
    assert not archive.exists()
    assert "refusing to replace" in capsys.readouterr().err
