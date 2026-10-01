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
from remote_code_bridge.uninstall import uninstall

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake remote needs a POSIX shell")


class FakeServices:
    """Behaves like ServiceManager on Linux, minus systemctl: writes and deletes the unit file."""

    def __init__(self, home):
        self.events = []
        self.unit = home / ".config" / "systemd" / "user" / "remote-code-bridge.service"

    def files(self):
        return [self.unit]

    def stop(self):
        self.events.append("stop")

    def install(self, python):
        self.events.append(("install", python))
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        self.unit.write_text(f"ExecStart={python} -m remote_code_bridge serve\n")

    def remove(self):
        self.events.append("remove")
        if self.unit.exists():
            self.unit.unlink()


@pytest.fixture
def world(home, fake_ssh, tmp_path, monkeypatch):
    """A host (HOME) with VS Code, an ssh config, and a reachable Linux remote with python3."""
    # Short remote HOME so the Unix socket path stays under the ~100 byte limit.
    remote = Path(tempfile.mkdtemp(prefix="rcb", dir="/tmp"))
    (remote / ".bashrc").write_text("# the remote user's own settings\nalias ll='ls -l'\n")
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
    world.home, world.remote, world.fakebin, world.calls = home, remote, fakebin, fake_ssh
    world.services = FakeServices(home)
    yield world
    shutil.rmtree(remote, ignore_errors=True)


def run_install(world, alias="devbox", service=False, **kwargs):
    install(alias, assume_yes=True, service=service, services=world.services, verify=False, **kwargs)
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
    # The host command comes from uv/pip, so nothing is added to the host's PATH or ~/.local/bin.
    assert not (world.home / ".zshrc").exists() and not (world.home / ".local" / "bin").exists()
    assert world.services.events == ["stop"]  # no login service unless asked for
    assert not list(remote_bin.glob(".remote-code-bridge*")), "temporary archive left behind"


def test_service_is_optional_and_remembered(world):
    run_install(world, service=True)
    assert world.services.events[-1] == ("install", sys.executable)
    assert world.services.unit.exists()
    world.services.events.clear()
    install("devbox", assume_yes=True, services=world.services, verify=False)  # no flag: keep the choice
    assert world.services.events[-1][0] == "install"
    run_install(world, service=False)  # switching it off removes it
    assert world.services.events[-1] == "remove" and not world.services.unit.exists()


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


def test_symlinked_remote_rc_file_is_followed_and_restored(world, tmp_path):
    dotfiles = world.remote / "dotfiles-bashrc"
    dotfiles.write_text("alias ll='ls -l'\n")
    (world.remote / ".bashrc").unlink()
    (world.remote / ".bashrc").symlink_to(dotfiles)
    run_install(world)
    assert (world.remote / ".bashrc").is_symlink()
    assert "remote-code-bridge PATH" in dotfiles.read_text()
    uninstall(assume_yes=True, services=world.services)
    assert dotfiles.read_text() == "alias ll='ls -l'\n" and (world.remote / ".bashrc").is_symlink()


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


# -- uninstall: no trace on either machine ------------------------------------------------------


def snapshot(root):
    """Every path under `root` with its content (or link target): the exact state of a home directory."""
    state = {}
    for directory, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                state[relative] = ("link", os.readlink(path))
            elif path.is_dir():
                state[relative] = ("dir", None)
            else:
                state[relative] = ("file", path.read_bytes())
    return state


@pytest.mark.parametrize("service", [False, True], ids=["no-service", "service"])
def test_uninstall_leaves_no_trace_on_either_machine(world, service):
    host_before, remote_before = snapshot(world.home), snapshot(world.remote)
    run_install(world, service=service)
    run_install(world)  # a reinstall/update in between must not lose track of anything
    assert snapshot(world.remote) != remote_before

    uninstall(assume_yes=True, services=world.services)

    assert snapshot(world.home) == host_before
    assert snapshot(world.remote) == remote_before
    assert ("remove" in world.services.events) is service


def test_uninstall_keeps_directories_and_files_that_were_already_there(world):
    """~/.local/bin and an existing ~/.profile are not ours: only our lines and files go."""
    (world.remote / ".local" / "bin").mkdir(parents=True)
    (world.remote / ".local" / "bin" / "my-tool").write_text("#!/bin/sh\n")
    remote_before = snapshot(world.remote)
    run_install(world)
    uninstall(assume_yes=True, services=world.services)
    assert snapshot(world.remote) == remote_before


@pytest.mark.parametrize("service", [False, True], ids=["no-service", "service"])
def test_unreachable_remote_changes_nothing_unless_host_only(world, monkeypatch, service):
    host_before = snapshot(world.home)
    run_install(world, service=service)
    installed = snapshot(world.home)
    world.services.events.clear()
    monkeypatch.setenv("FAKE_SSH_UNREACHABLE", "devbox")
    with pytest.raises(BridgeError, match="could not uninstall on devbox"):
        uninstall(assume_yes=True, services=world.services)
    assert snapshot(world.home) == installed  # untouched, and the service is running again
    if service:
        assert world.services.events == ["stop", ("install", sys.executable)]
    uninstall(assume_yes=True, host_only=True, services=world.services)
    assert snapshot(world.home) == host_before


def test_uninstall_refuses_while_serve_is_running(world, monkeypatch):
    run_install(world)
    monkeypatch.setattr("remote_code_bridge.uninstall.bridge_is_running", lambda: True)
    with pytest.raises(BridgeError, match="stop `remote-code-bridge serve` first"):
        uninstall(assume_yes=True, services=world.services)


def test_uninstall_without_install_is_a_no_op(world, capsys):
    uninstall(assume_yes=True, services=world.services)
    assert "nothing to uninstall" in capsys.readouterr().out
