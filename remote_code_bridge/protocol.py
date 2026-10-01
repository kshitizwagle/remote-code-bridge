"""What the host bridge does with one HTTP request. No sockets here, so it is easy to test.

Routes:
  GET  /healthz  no auth; reports service and tunnel state
  POST /check    auth only; lets `status` on the remote confirm the token matches
  POST /open     auth; validates the request and launches VS Code
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from secrets import compare_digest
from typing import Any, Callable

from remote_code_bridge import APP_NAME, MAX_REQUEST_BYTES, SECRET_ENV, __version__, is_valid_alias
from remote_code_bridge.config import HostConfig

# `code` flags that are forwarded. --goto is moved next to the path, where VS Code expects it.
SAFE_FLAGS = ("--reuse-window", "-r", "--new-window", "-n", "--goto", "-g")
GOTO_FLAGS = ("--goto", "-g")
# cmd.exe interprets these even inside quotes, so they can't be passed safely to code.cmd.
CMD_UNSAFE = set('"%!^&|<>')
MAX_CHILDREN = 8


@dataclass
class HttpRequest:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)  # names are lower-case
    body: bytes = b""


@dataclass
class Response:
    status: int
    payload: dict[str, Any]

    @classmethod
    def error(cls, status: int, message: str) -> Response:
        return cls(status, {"ok": False, "error": message})

    def body(self) -> bytes:
        return json.dumps(self.payload, separators=(",", ":")).encode("utf-8")


class LaunchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Launcher:
    """Starts VS Code without a shell, and caps how many launches can be in flight."""

    def __init__(self, max_children: int = MAX_CHILDREN):
        self._slots = threading.BoundedSemaphore(max_children)

    def launch(self, command: list[str]) -> None:
        if not self._slots.acquire(blocking=False):
            raise LaunchError(503, "too many VS Code requests in flight")
        try:
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={key: value for key, value in os.environ.items() if key not in SECRET_ENV},
            )
        except OSError as error:
            self._slots.release()
            raise LaunchError(500, f"failed to launch VS Code: {error}") from error
        threading.Thread(target=self._reap, args=(child,), daemon=True).start()

    def _reap(self, child: subprocess.Popen) -> None:
        try:
            child.wait()
        finally:
            self._slots.release()


def build_code_command(config: HostConfig, host: str, path: str, args: list[str]) -> list[str]:
    forwarded = [arg for arg in args if arg in SAFE_FLAGS and arg not in GOTO_FLAGS]
    goto = [arg for arg in args if arg in GOTO_FLAGS][:1]
    return [config.code_bin, *forwarded, "--remote", f"ssh-remote+{host}", *goto, path]


def handle_request(
    config: HostConfig,
    request: HttpRequest,
    launcher: Launcher,
    tunnel_status: Callable[[], dict[str, Any]] | None = None,
) -> Response:
    if request.method == "GET" and request.path == "/healthz":
        if request.body:
            return Response.error(400, "invalid request size")
        tunnel = tunnel_status() if tunnel_status else {"state": "disabled"}
        return Response(200, {"ok": True, "service": APP_NAME, "version": __version__, "tunnel": tunnel})
    if request.method != "POST" or request.path not in ("/open", "/check"):
        return Response.error(404, "not found")
    if not request.body or len(request.body) > MAX_REQUEST_BYTES:
        return Response.error(400, "invalid request size")
    if not config.token:
        return Response.error(500, "REMOTE_CODE_BRIDGE_TOKEN is not set on host")
    authorization = request.headers.get("authorization", "")
    if not compare_digest(authorization.encode("utf-8"), f"Bearer {config.token}".encode()):
        return Response.error(401, "unauthorized")
    if request.path == "/check":
        return Response(200, {"ok": True})
    return _open(config, request.body, launcher)


def _open(config: HostConfig, body: bytes, launcher: Launcher) -> Response:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Response.error(400, "invalid json")
    if not isinstance(payload, dict):
        return Response.error(400, "invalid json")
    path, host, args = payload.get("path"), payload.get("host"), payload.get("args") or []
    if not isinstance(path, str) or not (host is None or isinstance(host, str)):
        return Response.error(400, "invalid json")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return Response.error(400, "invalid json")

    # The path is on the Linux remote, so it is checked with POSIX rules even on a Windows host.
    if not posixpath.isabs(path):
        return Response.error(400, "path must be an absolute remote path")
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in path):
        return Response.error(400, "invalid remote path")
    host = host or config.default_host
    if not host:
        return Response.error(400, "host alias is required")
    if not is_valid_alias(host):
        return Response.error(400, "invalid host alias")
    if config.allowed_hosts is not None and host not in config.allowed_hosts:
        return Response.error(403, f"host alias not allowed: {host}")
    if config.code_bin.lower().endswith((".cmd", ".bat")) and CMD_UNSAFE.intersection(path):
        return Response.error(400, "path contains characters unsafe for code.cmd")

    command = build_code_command(config, host, path, args)
    if config.dry_run:
        return Response(200, {"ok": True, "dry_run": True, "command": command})
    if shutil.which(config.code_bin) is None:
        return Response.error(500, f"'{config.code_bin}' not found in PATH")
    try:
        launcher.launch(command)
    except LaunchError as error:
        return Response.error(error.status, str(error))
    return Response(200, {"ok": True, "command": command})
