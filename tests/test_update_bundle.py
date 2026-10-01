import os
import socket
import subprocess
import sys
import time

import pytest
from conftest import TOKEN

from remote_code_bridge import BridgeError, bundle, update

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="runs the archive through a `code` symlink")


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    return bundle.build(tmp_path_factory.mktemp("dist") / "remote-code-bridge.pyz", version="9.8.7")


def test_built_archive_runs_and_carries_the_version(archive):
    out = subprocess.run([sys.executable, str(archive), "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "remote-code-bridge 9.8.7"


def test_archive_knows_it_is_an_archive(archive):
    code = "from remote_code_bridge.bundle import running_archive; print(running_archive())"
    env = dict(os.environ, PYTHONPATH=str(archive))
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=archive.parent, capture_output=True, text=True)
    assert out.stdout.strip() == str(archive)
    assert bundle.running_archive() is None  # tests run from the source checkout
    assert bundle.archive_bytes()[:2] == b"#!"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@posix_only
def test_full_stack_through_the_archive(archive, tmp_path, home):
    """`serve` and `code` from the release file, talking over HTTP like the real thing."""
    port = free_port()
    env = dict(os.environ, REMOTE_CODE_BRIDGE_TOKEN=TOKEN, REMOTE_CODE_BRIDGE_PORT=str(port),
               REMOTE_CODE_BRIDGE_DEFAULT_HOST="devbox", REMOTE_CODE_BRIDGE_DRY_RUN="1",
               REMOTE_CODE_BRIDGE_TUNNEL="0")  # fmt: skip
    server = subprocess.Popen([sys.executable, str(archive), "serve"], env=env, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.05)
        code = tmp_path / "code"
        code.symlink_to(archive)
        archive.chmod(0o755)
        client_env = dict(env, REMOTE_CODE_BRIDGE_HOST_ALIAS="devbox")
        out = subprocess.run([str(code), "--reuse-window", "."], cwd=tmp_path, env=client_env,
                             capture_output=True, text=True)  # fmt: skip
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == (
            f"dry-run command: code --reuse-window --remote ssh-remote+devbox {os.path.realpath(tmp_path)}"
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
    assert b"stopped" in server.stderr.read()  # SIGTERM shuts down cleanly


# -- update ---------------------------------------------------------------------------------------


class StopRecorder:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture
def saved_alias(home):
    config = home / ".config" / "remote-code-bridge"
    config.mkdir(parents=True)
    (config / "host.env").write_text("REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\n")


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_run(argv, env):
        calls.append((argv, env))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    monkeypatch.setattr(update.sys, "platform", "linux")
    return calls


def test_update_with_uv_upgrades_then_reinstalls(saved_alias, ran, monkeypatch, capsys):
    monkeypatch.setattr(update, "installed_with_uv", lambda: True)
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    services = StopRecorder()
    assert update.update(services=services) == 0
    assert services.stopped  # the service runs from the environment being replaced
    (upgrade, env), (reinstall, _) = ran
    assert upgrade == ["/bin/uv", "tool", "install", "--force", "--reinstall", update.PACKAGE]
    assert reinstall == ["/bin/remote-code-bridge", "install", "devbox", "--yes"]
    assert "GH_TOKEN" not in env
    assert "updated for SSH alias devbox" in capsys.readouterr().out


def test_update_with_pip_and_a_custom_source(saved_alias, ran, monkeypatch):
    monkeypatch.setattr(update, "installed_with_uv", lambda: False)
    monkeypatch.setenv("RCB_PACKAGE_SPEC", "/src/my-fork")
    update.update("lab", services=StopRecorder())
    assert ran[0][0] == [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps",
                         "/src/my-fork"]  # fmt: skip
    assert ran[1][0][1:] == ["install", "lab", "--yes"]


def test_update_reports_a_failed_upgrade(saved_alias, monkeypatch):
    monkeypatch.setattr(update, "installed_with_uv", lambda: False)
    monkeypatch.setattr(update.sys, "platform", "linux")
    monkeypatch.setattr(update.subprocess, "run", lambda argv, env: subprocess.CompletedProcess(argv, 1))
    with pytest.raises(BridgeError, match="upgrading the package failed"):
        update.update(services=StopRecorder())


def test_update_needs_uv_when_installed_with_uv(saved_alias, monkeypatch):
    monkeypatch.setattr(update, "installed_with_uv", lambda: True)
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    with pytest.raises(BridgeError, match="`uv` is not on PATH"):
        update.update(services=StopRecorder())


def test_update_needs_a_valid_alias(home):
    with pytest.raises(BridgeError, match="could not determine SSH alias"):
        update.update(services=StopRecorder())
    with pytest.raises(BridgeError, match="unsupported characters"):
        update.update("-oProxyCommand=x", services=StopRecorder())


def test_windows_update_continues_after_this_process_exits(saved_alias, monkeypatch):
    """Windows can't replace the running python.exe, so a helper script does the work afterwards."""
    started = []
    monkeypatch.setattr(update, "installed_with_uv", lambda: False)
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(update.subprocess, "Popen", lambda argv, **kwargs: started.append(argv))
    assert update.update(services=StopRecorder()) == 0
    script = started[0][-1]
    text = open(script).read()
    assert "pip install --upgrade" in text and "remote-code-bridge install devbox --yes" in text
    assert text.index("ping") < text.index("pip install")  # waits for this process to exit first
