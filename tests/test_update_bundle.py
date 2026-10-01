import hashlib
import http.server
import os
import socket
import subprocess
import sys
import threading
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


@pytest.fixture
def release(tmp_path, archive):
    directory = tmp_path / "release"
    directory.mkdir()
    data = archive.read_bytes()
    (directory / "remote-code-bridge.pyz").write_bytes(data)
    (directory / "remote-code-bridge.pyz.sha256").write_text(
        f"{hashlib.sha256(data).hexdigest()}  remote-code-bridge.pyz\n"
    )
    return directory


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_run(argv, env):
        calls.append((argv, env))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    return calls


def test_update_runs_the_new_installer_for_the_saved_alias(home, release, ran, monkeypatch, capsys):
    config = home / ".config" / "remote-code-bridge"
    config.mkdir(parents=True)
    (config / "host.env").write_text("REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\n")
    monkeypatch.setenv("RCB_RELEASE_URL", release.as_uri() + "/")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    assert update.update() == 0
    argv, env = ran[0]
    assert argv[0] == sys.executable and argv[1].endswith("remote-code-bridge.pyz")
    assert argv[2:] == ["install", "devbox", "--yes"]
    assert "GH_TOKEN" not in env
    assert "updated for SSH alias devbox" in capsys.readouterr().out


def test_update_rejects_a_bad_checksum(home, release, ran, monkeypatch):
    (release / "remote-code-bridge.pyz.sha256").write_text("0" * 64 + "\n")
    monkeypatch.setenv("RCB_RELEASE_URL", release.as_uri())
    with pytest.raises(BridgeError, match="checksum failed"):
        update.update("devbox")
    assert ran == []


def test_update_needs_a_valid_alias(home, ran):
    with pytest.raises(BridgeError, match="could not determine SSH alias"):
        update.update()
    with pytest.raises(BridgeError, match="unsupported characters"):
        update.update("-oProxyCommand=x")


class RateLimited(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/missing":
            self.send_response(404)
        elif self.headers.get("Authorization") == "Bearer ghp_ok":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        else:
            self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def github():
    server = http.server.HTTPServer(("127.0.0.1", 0), RateLimited)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_download_retries_with_gh_token_after_rate_limit(github, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_ok")
    assert update.download(f"{github}/file") == b"ok"


def test_download_errors(github, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(BridgeError, match="HTTP 403. If GitHub rate-limited you, export GH_TOKEN"):
        update.download(f"{github}/file")
    with pytest.raises(BridgeError, match="HTTP 404"):
        update.download(f"{github}/missing")
    monkeypatch.setenv("GH_TOKEN", "ghp_wrong")
    with pytest.raises(BridgeError, match="even with GH_TOKEN"):
        update.download(f"{github}/file")
    with pytest.raises(BridgeError, match="download failed"):
        update.download("http://127.0.0.1:1/nothing")
