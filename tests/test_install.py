"""The installer, end to end, against tests/fakes/fake_ssh.py.

The fake runs "remote" commands locally with HOME pointing at a separate directory, so these
tests check the real files the installer writes on both sides. POSIX only (needs `sh`)."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import TOKEN

from remote_code_bridge import BridgeError
from remote_code_bridge.config import read_values
from remote_code_bridge.install import install

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake remote needs a POSIX shell")


class FakeServices:
    def __init__(self):
        self.events = []

    def stop(self):
        self.events.append("stop")

    def install(self, python, archive):
        self.events.append(("install", python, Path(archive)))


@pytest.fixture
def world(home, fake_ssh, tmp_path, monkeypatch):
    """A host (HOME) with VS Code, an ssh config, and a reachable Linux remote with python3."""
    # Short remote HOME so the Unix socket path stays under the ~100 byte limit.
    remote = Path(tempfile.mkdtemp(prefix="rcb", dir="/tmp"))
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    (fakebin / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo "${FAKE_UNAME:-Linux}";; *) echo x86_64;; esac\n')
    (fakebin / "uname").chmod(0o755)
    os.symlink(sys.executable, fakebin / "python3")
    hostbin = tmp_path / "hostbin"
    hostbin.mkdir()
    (hostbin / "code").write_text("#!/bin/sh\n")
    (hostbin / "code").chmod(0o755)

    monkeypatch.setenv("PATH", f"{hostbin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REMOTE_HOME", str(remote))
    monkeypatch.setenv("FAKE_REMOTE_PATH", str(fakebin))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("FAKE_SSH_HOSTS", json.dumps({"devbox": "10.0.0.5 me 22", "lab": "10.0.0.5 me 22"}))
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text("Host devbox\n  HostName 10.0.0.5\nHost lab\n  HostName 10.0.0.5\nHost dead\n")
    (ssh_dir / "config").chmod(0o600)
    monkeypatch.setenv("FAKE_SSH_UNREACHABLE", "dead")

    class World:
        pass

    world = World()
    world.home, world.remote, world.fakebin, world.calls, world.services = (
        home,
        remote,
        fakebin,
        fake_ssh,
        FakeServices(),
    )
    yield world
    shutil.rmtree(remote, ignore_errors=True)


def run_install(world, alias="devbox", **kwargs):
    install(alias, assume_yes=True, services=world.services, verify=False, **kwargs)
    return read_values(world.home / ".config" / "remote-code-bridge" / "host.env")


def test_fresh_install_writes_both_sides(world):
    host = run_install(world)
    remote_env = read_values(world.remote / ".config" / "remote-code-bridge" / "remote.env")

    assert host["REMOTE_CODE_BRIDGE_DEFAULT_HOST"] == "devbox"
    assert host["REMOTE_CODE_BRIDGE_ALLOWED_HOSTS"] == "devbox,lab"  # same machine, both aliases allowed
    assert host["REMOTE_CODE_BRIDGE_TUNNEL_ALIAS"] == "devbox"
    assert host["REMOTE_CODE_BRIDGE_CODE_BIN"].endswith("hostbin/code")
    assert host["REMOTE_CODE_BRIDGE_TOKEN"] == remote_env["REMOTE_CODE_BRIDGE_TOKEN"]
    assert host["REMOTE_CODE_BRIDGE_TUNNEL_SOCKET"] == remote_env["REMOTE_CODE_BRIDGE_SOCKET"]
    assert remote_env["REMOTE_CODE_BRIDGE_SOCKET"] == str(world.remote / ".cache/remote-code-bridge/bridge.sock")
    assert oct((world.remote / ".cache/remote-code-bridge").stat().st_mode & 0o777) == "0o700"

    remote_bin = world.remote / ".local" / "bin"
    assert os.readlink(remote_bin / "code") == "remote-code-bridge"
    assert os.access(remote_bin / "remote-code-bridge", os.X_OK)
    assert "remote-code-bridge PATH" in (world.remote / ".bashrc").read_text()
    assert "remote-code-bridge PATH" in (world.home / ".zshrc").read_text()
    launcher = (world.home / ".local" / "bin" / "remote-code-bridge").read_text()
    assert sys.executable in launcher and "remote-code-bridge.pyz" in launcher
    assert world.services.events[0] == "stop" and world.services.events[1][0] == "install"
    assert not list(remote_bin.glob(".remote-code-bridge.*")), "temporary archive left behind"


def test_token_is_never_on_a_command_line(world):
    host = run_install(world)
    for call in world.calls():
        assert host["REMOTE_CODE_BRIDGE_TOKEN"] not in json.dumps(call)


def test_every_helper_connection_ignores_forwards(world):
    """B3: v1's probes asked for the RemoteForward too, and failed while you were connected."""
    run_install(world)
    for call in world.calls():
        if "-G" not in call["argv"]:
            assert "ClearAllForwardings=yes" in call["argv"] and "ControlPath=none" in call["argv"]


def test_rerun_keeps_token_port_and_custom_settings(world):
    config = world.home / ".config" / "remote-code-bridge" / "host.env"
    config.parent.mkdir(parents=True)
    config.write_text(f"# my note\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\nREMOTE_CODE_BRIDGE_PORT=4123\nMY_SETTING=1\n")
    host = run_install(world)
    assert host["REMOTE_CODE_BRIDGE_TOKEN"] == TOKEN
    assert host["REMOTE_CODE_BRIDGE_PORT"] == "4123"  # B5: v1 always wrote 39731
    assert host["MY_SETTING"] == "1" and "# my note" in config.read_text()
    run_install(world)
    assert (world.remote / ".bashrc").read_text().count("remote-code-bridge PATH >>>") == 1


def test_discovery_skips_unreachable_and_groups_same_machine(world):
    host = run_install(world, alias=None)
    assert host["REMOTE_CODE_BRIDGE_DEFAULT_HOST"] == "devbox"


def test_discovery_refuses_to_guess_between_machines(world, monkeypatch):
    """B13: v1 silently took the first reachable alias."""
    monkeypatch.setenv("FAKE_SSH_HOSTS", json.dumps({"devbox": "10.0.0.5 me 22", "lab": "10.0.0.9 me 22"}))
    with pytest.raises(BridgeError, match=r"several Linux remotes are reachable \(devbox, lab\)"):
        run_install(world, alias=None)


def test_unresolvable_alias_is_skipped(world, monkeypatch):
    """B6: v1 on Windows aborted the whole install when `ssh -G` failed for any alias."""
    monkeypatch.setenv("FAKE_SSH_G_FAIL", "lab")
    assert run_install(world)["REMOTE_CODE_BRIDGE_ALLOWED_HOSTS"] == "devbox"


def test_unrelated_remote_code_is_refused_with_a_clear_message(world):
    """B14: v1 on Windows reported this as an opaque ssh failure."""
    (world.remote / ".local" / "bin").mkdir(parents=True)
    (world.remote / ".local" / "bin" / "code").write_text("#!/bin/sh\necho real vscode server\n")
    with pytest.raises(BridgeError, match="refusing to replace .*code: it is not a remote-code-bridge link"):
        run_install(world)


def test_v1_wrapper_and_link_are_replaced(world):
    bin_dir = world.remote / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "code").write_text("#!/bin/sh\n# Remote-side wrapper for remote-code-bridge.\n")
    run_install(world)
    assert os.readlink(bin_dir / "code") == "remote-code-bridge"


def test_v1_ssh_forward_is_removed(world):
    config = world.home / ".ssh" / "config"
    config.write_text(
        "# >>> remote-code-bridge include >>>\nInclude ~/.ssh/remote-code-bridge/config\n"
        "# <<< remote-code-bridge include <<<\n" + config.read_text()
    )
    managed = world.home / ".ssh" / "remote-code-bridge"
    managed.mkdir()
    (managed / "config").write_text("Host devbox\n    RemoteForward 127.0.0.1:39731 127.0.0.1:39731\n")
    run_install(world)
    assert "remote-code-bridge" not in config.read_text()
    assert "Host devbox" in config.read_text()
    assert not managed.exists()


def test_symlinked_rc_files_are_followed(world, tmp_path):
    dotfiles = tmp_path / "dotfiles-zshrc"
    dotfiles.write_text("alias ll='ls -l'\n")
    (world.home / ".zshrc").symlink_to(dotfiles)
    run_install(world)
    assert (world.home / ".zshrc").is_symlink()
    assert "remote-code-bridge PATH" in dotfiles.read_text()


@pytest.mark.parametrize(
    "setting,message",
    [
        ({"FAKE_UNAME": "Darwin"}, "runs Darwin; the remote must be Linux"),
        ({"FAKE_SSH_UNREACHABLE": "devbox"}, "cannot reach SSH alias devbox"),
    ],
)
def test_explicit_alias_checks(world, monkeypatch, setting, message):
    for key, value in setting.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(BridgeError, match=message):
        run_install(world)


def test_old_remote_python_is_refused(world):
    (world.fakebin / "python3").unlink()
    (world.fakebin / "python3").write_text("#!/bin/sh\necho 3 6\n")
    (world.fakebin / "python3").chmod(0o755)
    with pytest.raises(BridgeError, match=r"needs python3 3.8 or newer \(found python 3.6\)"):
        run_install(world)


def test_missing_vscode_is_reported(world, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([str(world.fakebin), "/usr/bin", "/bin"]))
    with pytest.raises(BridgeError, match="could not find VS Code"):
        run_install(world)


def test_bad_alias_and_empty_config(world):
    with pytest.raises(BridgeError, match="unsupported characters"):
        run_install(world, alias="-oProxyCommand=x")
    (world.home / ".ssh" / "config").write_text("")
    with pytest.raises(BridgeError, match="no concrete SSH Host aliases"):
        run_install(world, alias=None)
