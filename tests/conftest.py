import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE_SSH = Path(__file__).resolve().parent / "fakes" / "fake_ssh.py"
TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME, so nothing touches the real ~/.config or ~/.ssh."""
    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    for name in list(os.environ):
        if name.startswith("REMOTE_CODE_BRIDGE_") or name in ("GH_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.delenv(name)
    return home


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    """Route every ssh call to tests/fakes/fake_ssh.py; returns a reader for the call log."""
    log = tmp_path / "ssh-calls.jsonl"
    monkeypatch.setenv("RCB_SSH", json.dumps([sys.executable, str(FAKE_SSH)]))
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return calls
