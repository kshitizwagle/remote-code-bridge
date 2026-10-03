"""The remote side: `code .` turns into one authenticated POST through the SSH tunnel."""

from __future__ import annotations

import json
import os
import posixpath
import socket
from dataclasses import dataclass, field
from typing import Any, Iterable

from remote_code_bridge import MAX_REQUEST_BYTES, BridgeError
from remote_code_bridge.config import RemoteConfig
from remote_code_bridge.httpio import DeadlineReader, HttpError, format_request

TIMEOUT = 5.0
FORWARDED_FLAGS = ("-r", "--reuse-window", "-n", "--new-window", "-g", "--goto")
REJECTED_FLAGS = ("--wait", "--diff", "--merge", "--install-extension", "--uninstall-extension", "--list-extensions")
NO_TUNNEL = "no tunnel from your host. Is the host awake and logged in? Run 'remote-code-bridge status' on the host."


@dataclass
class OpenRequest:
    path: str
    args: list[str] = field(default_factory=list)
    host: str | None = None
    folder: bool = False


def parse_open_args(arguments: Iterable[str], cwd: str | None = None) -> OpenRequest:
    flags: list[str] = []
    path: str | None = None
    after_separator = False
    for raw in arguments:
        if not after_separator and raw == "--":
            after_separator = True
            continue
        if not after_separator and raw in FORWARDED_FLAGS:
            flags.append(raw)
            continue
        if not after_separator and raw in REJECTED_FLAGS:
            raise BridgeError(f"unsupported code flag for remote bridge: {raw}")
        if not after_separator and raw.startswith("-"):
            raise BridgeError(f"unsupported flag: {raw}")
        if path is not None:
            raise BridgeError("only one path is supported")
        path = raw
    resolved = resolve_remote_path(path or ".", cwd)
    return OpenRequest(path=resolved, args=flags, folder=os.path.isdir(resolved))


def resolve_remote_path(path: str, cwd: str | None = None) -> str:
    """Make the path absolute and collapse `.` and `..`."""
    if posixpath.isabs(path):
        return posixpath.normpath(path)
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError as error:
            raise BridgeError(f"could not resolve current directory: {error}") from error
    return posixpath.normpath(posixpath.join(cwd, path))


def connect(config: RemoteConfig) -> socket.socket:
    try:
        if config.socket:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
            sock.settimeout(TIMEOUT)
            sock.connect(config.socket)
        else:
            sock = socket.create_connection(("127.0.0.1", config.port), timeout=TIMEOUT)
    except (FileNotFoundError, ConnectionRefusedError) as error:
        raise BridgeError(NO_TUNNEL) from error
    except OSError as error:
        raise BridgeError(f"could not reach host bridge: {error}") from error
    return sock


def call(config: RemoteConfig, method: str, path: str, body: bytes = b"", token: str = "") -> dict[str, Any]:
    """Send one request and return the JSON reply, whatever its HTTP status."""
    sock = connect(config)
    try:
        sock.sendall(format_request(method, path, body, token))
        response = DeadlineReader(sock, TIMEOUT, "host bridge response timed out").read_to_end(MAX_REQUEST_BYTES)
    except HttpError as error:
        raise BridgeError(str(error)) from error
    except OSError as error:
        raise BridgeError(f"could not talk to host bridge: {error}") from error
    finally:
        sock.close()
    _head, separator, payload = response.partition(b"\r\n\r\n")
    if not separator:
        raise BridgeError("host bridge returned an invalid HTTP response")
    try:
        result = json.loads(payload.decode("utf-8"))
    except ValueError as error:
        raise BridgeError("host bridge returned invalid JSON") from error
    if not isinstance(result, dict):
        raise BridgeError("host bridge returned invalid JSON")
    return result


def send_open_request(config: RemoteConfig, request: OpenRequest) -> dict[str, Any]:
    if not config.host_alias:
        raise BridgeError("REMOTE_CODE_BRIDGE_HOST_ALIAS is not set")
    if not config.token:
        raise BridgeError("REMOTE_CODE_BRIDGE_TOKEN is not set")
    payload = {"host": config.host_alias, "path": request.path, "args": request.args, "folder": request.folder}
    body = json.dumps(payload).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise BridgeError("request is too large")
    return call(config, "POST", "/open", body, config.token)


def open_in_vscode(config: RemoteConfig, arguments: Iterable[str]) -> str:
    """Run the whole `code ...` flow and return the line to print."""
    reply = send_open_request(config, parse_open_args(arguments))
    if not reply.get("ok"):
        raise BridgeError(str(reply.get("error") or "request failed"))
    if reply.get("dry_run"):
        return "dry-run command: " + " ".join(str(part) for part in reply.get("command", []))
    return "Opening VS Code on host"
