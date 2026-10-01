import json
import sys

import pytest

from remote_code_bridge.config import HostConfig
from remote_code_bridge.protocol import HttpRequest, Launcher, LaunchError, Response, build_code_command, handle_request

CONFIG = HostConfig(
    token="token", code_bin="/usr/bin/code", default_host="devbox", allowed_hosts=frozenset(["devbox"]), dry_run=True
)


class RecordingLauncher(Launcher):
    def __init__(self, error=None):
        super().__init__()
        self.commands, self.error = [], error

    def launch(self, command):
        if self.error:
            raise self.error
        self.commands.append(command)


def post(body, authorization="Bearer token", path="/open"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpRequest("POST", path, {"authorization": authorization}, raw)


def handle(request, config=CONFIG, launcher=None, tunnel_status=None):
    return handle_request(config, request, launcher or RecordingLauncher(), tunnel_status)


def test_healthz_is_public_and_reports_tunnel():
    response = handle(HttpRequest("GET", "/healthz"), tunnel_status=lambda: {"state": "up"})
    assert response.status == 200
    assert response.payload["service"] == "remote-code-bridge"
    assert response.payload["tunnel"] == {"state": "up"}


def test_healthz_with_body_is_rejected():
    assert handle(HttpRequest("GET", "/healthz", body=b"nope")) == Response.error(400, "invalid request size")


def test_unknown_route():
    assert handle(HttpRequest("GET", "/open")) == Response.error(404, "not found")


def test_open_requires_exact_bearer_token():
    assert handle(post({"path": "/srv"}, "Bearer incorrect")) == Response.error(401, "unauthorized")
    assert handle(post({"path": "/srv"}, "")) == Response.error(401, "unauthorized")


def test_check_only_verifies_the_token():
    assert handle(post(b"{}", path="/check")) == Response(200, {"ok": True})
    assert handle(post(b"{}", "Bearer nope", path="/check")).status == 401


def test_missing_host_token_is_a_server_error():
    assert handle(post({"path": "/srv"}), HostConfig()).status == 500


@pytest.mark.parametrize("body", [b"", b"x" * (64 * 1024 + 1)], ids=["empty", "too-big"])
def test_request_size_limits(body):
    assert handle(post(body)) == Response.error(400, "invalid request size")


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", {"path": 1}, {"path": "/a", "args": "x"}, {"path": "/a", "host": 3}]
)
def test_invalid_json(body):
    assert handle(post(body)) == Response.error(400, "invalid json")


def test_dry_run_returns_the_safe_command():
    response = handle(post({"path": "/srv/project", "args": ["--reuse-window", "--bad", "-g"]}))
    assert response.payload == {
        "ok": True,
        "dry_run": True,
        "command": ["/usr/bin/code", "--reuse-window", "--remote", "ssh-remote+devbox", "-g", "/srv/project"],
    }


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"path": "project"}, Response.error(400, "path must be an absolute remote path")),
        ({"path": "/srv/project", "host": "other"}, Response.error(403, "host alias not allowed: other")),
        ({"path": "/srv/project", "host": "-option"}, Response.error(400, "invalid host alias")),
        ({"path": "/srv/\u0000project"}, Response.error(400, "invalid remote path")),
        ({"path": "/srv/\u0085project"}, Response.error(400, "invalid remote path")),
    ],
)
def test_validation_errors(body, expected):
    assert handle(post(body)) == expected


def test_host_alias_is_required_without_default():
    config = HostConfig(token="token", dry_run=True)
    assert handle(post({"path": "/srv"}), config) == Response.error(400, "host alias is required")


def test_posix_paths_are_absolute_even_for_windows_hosts():
    """B16: version 1 treated /home/... as a relative path on Windows hosts and rejected it."""
    config = HostConfig(token="token", code_bin=r"C:\VS Code\bin\code.cmd", default_host="devbox", dry_run=True)
    assert handle(post({"path": "/home/u/project"}), config).status == 200


@pytest.mark.parametrize("path", ["/a&calc", "/a|b", "/100%", '/a"b', "/a^b", "/a<b", "/a!b"])
def test_cmd_metacharacters_rejected_for_cmd_launchers(path):
    """B10: cmd.exe would interpret these when VS Code is started through code.cmd."""
    config = HostConfig(token="token", code_bin=r"C:\VS Code\bin\code.cmd", default_host="devbox", dry_run=True)
    assert handle(post({"path": path}), config) == Response.error(400, "path contains characters unsafe for code.cmd")
    assert handle(post({"path": path})).status == 200  # fine for a real executable


def test_command_builder_never_forwards_unsafe_flags():
    assert build_code_command(CONFIG, "devbox", "/safe path", ["--new-window", "--command=evil", "--goto", "-g"]) == [
        "/usr/bin/code", "--new-window", "--remote", "ssh-remote+devbox", "--goto", "/safe path",
    ]  # fmt: skip


# Windows only runs files with a PATHEXT extension; VS Code's launcher there is code.cmd.
CODE_NAME = "code.cmd" if sys.platform == "win32" else "code"


def test_launch_uses_the_launcher(tmp_path):
    code = tmp_path / CODE_NAME
    code.write_text("#!/bin/sh\n")
    code.chmod(0o755)
    config = HostConfig(token="token", code_bin=str(code), default_host="devbox")
    launcher = RecordingLauncher()
    response = handle(post({"path": "/srv"}), config, launcher)
    assert response.status == 200
    assert launcher.commands == [[str(code), "--remote", "ssh-remote+devbox", "/srv"]]


def test_missing_code_binary(tmp_path):
    config = HostConfig(token="token", code_bin=str(tmp_path / "nope"), default_host="devbox")
    assert handle(post({"path": "/srv"}), config).status == 500


def test_launch_errors_are_reported(tmp_path):
    code = tmp_path / CODE_NAME
    code.write_text("")
    code.chmod(0o755)
    config = HostConfig(token="token", code_bin=str(code), default_host="devbox")
    launcher = RecordingLauncher(LaunchError(503, "too many VS Code requests in flight"))
    assert handle(post({"path": "/srv"}), config, launcher) == Response.error(
        503, "too many VS Code requests in flight"
    )


def test_real_launcher_bounds_children_and_reaps(tmp_path):
    import sys
    import time

    launcher = Launcher(max_children=1)
    launcher.launch([sys.executable, "-c", "import time; time.sleep(0.3)"])
    with pytest.raises(LaunchError) as busy:
        launcher.launch([sys.executable, "-c", "pass"])
    assert busy.value.status == 503
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:  # the slot comes back once the child exits
        try:
            launcher.launch([sys.executable, "-c", "pass"])
            break
        except LaunchError:
            time.sleep(0.05)
    else:
        pytest.fail("child slot was never released")
    with pytest.raises(LaunchError) as missing:
        Launcher().launch([str(tmp_path / "does-not-exist")])
    assert missing.value.status == 500
