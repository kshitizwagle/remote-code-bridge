#!/usr/bin/env python3
"""
Host-side daemon for remote-code-bridge.

Listens on localhost, receives authenticated open requests from a remote wrapper
through an SSH reverse tunnel, and launches VS Code Remote-SSH on the host.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

APP_NAME = "remote-code-bridge"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 39731


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def parse_allowed_hosts(raw: str | None) -> set[str] | None:
    if raw is None or raw.strip() == "":
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


class Config:
    bind: str = env("REMOTE_CODE_BRIDGE_BIND", DEFAULT_BIND) or DEFAULT_BIND
    port: int = int(env("REMOTE_CODE_BRIDGE_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    token: str | None = env("REMOTE_CODE_BRIDGE_TOKEN")
    code_bin: str = env("REMOTE_CODE_BRIDGE_CODE_BIN", "code") or "code"
    default_host: str | None = env("REMOTE_CODE_BRIDGE_DEFAULT_HOST")
    allowed_hosts: set[str] | None = parse_allowed_hosts(env("REMOTE_CODE_BRIDGE_ALLOWED_HOSTS"))
    dry_run: bool = env("REMOTE_CODE_BRIDGE_DRY_RUN", "0") in {"1", "true", "yes"}


CONFIG = Config()


def fail(message: str, status: int = 400) -> tuple[int, dict[str, Any]]:
    return status, {"ok": False, "error": message}


def ok(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return 200, {"ok": True, **payload}


def validate_request(headers: Any, payload: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    if not CONFIG.token:
        return fail("REMOTE_CODE_BRIDGE_TOKEN is not set on host", 500)

    auth = headers.get("Authorization", "")
    expected = f"Bearer {CONFIG.token}"
    if not secrets.compare_digest(auth, expected):
        return fail("unauthorized", 401)

    path = payload.get("path")
    host = payload.get("host") or CONFIG.default_host

    if not isinstance(path, str) or not path.startswith("/"):
        return fail("path must be an absolute remote path")

    if not isinstance(host, str) or not host:
        return fail("host alias is required")

    if any(ch in host for ch in ["/", "\\", "\x00", "\n", "\r"]):
        return fail("invalid host alias")

    if CONFIG.allowed_hosts is not None and host not in CONFIG.allowed_hosts:
        return fail(f"host alias not allowed: {host}", 403)

    args = payload.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        return fail("args must be a list of strings")

    return None


def build_code_command(payload: dict[str, Any]) -> list[str]:
    host = payload.get("host") or CONFIG.default_host
    path = payload["path"]
    args = payload.get("args") or []

    # Only forward a small safe subset of UX flags. Keep MVP predictable.
    allowed_passthrough_flags = {
        "--reuse-window",
        "-r",
        "--new-window",
        "-n",
        "--goto",
        "-g",
    }
    forwarded_args = [arg for arg in args if arg in allowed_passthrough_flags]

    return [
        CONFIG.code_bin,
        *forwarded_args,
        "--remote",
        f"ssh-remote+{host}",
        path,
    ]


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib interface
        if self.path == "/healthz":
            self.send_json(200, {"ok": True, "service": APP_NAME})
            return
        self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib interface
        if self.path != "/open":
            self.send_json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"ok": False, "error": "invalid content length"})
            return

        if length <= 0 or length > 64 * 1024:
            self.send_json(400, {"ok": False, "error": "invalid request size"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.send_json(400, {"ok": False, "error": "invalid json"})
            return

        if not isinstance(payload, dict):
            self.send_json(400, {"ok": False, "error": "json body must be an object"})
            return

        validation_error = validate_request(self.headers, payload)
        if validation_error is not None:
            status, body = validation_error
            self.send_json(status, body)
            return

        command = build_code_command(payload)

        if CONFIG.dry_run:
            self.send_json(200, {"ok": True, "dry_run": True, "command": command})
            return

        code_path = shutil.which(CONFIG.code_bin)
        if code_path is None:
            self.send_json(500, {"ok": False, "error": f"'{CONFIG.code_bin}' not found in PATH"})
            return

        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"failed to launch VS Code: {exc}"})
            return

        self.send_json(200, {"ok": True, "command": command})

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, indent=None, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[{APP_NAME}] {self.address_string()} - {fmt % args}\n")


def main() -> int:
    if CONFIG.bind != "127.0.0.1":
        print("Refusing to bind to non-localhost address by default.", file=sys.stderr)
        print("Use 127.0.0.1. The SSH reverse tunnel should provide remote access.", file=sys.stderr)
        return 2

    if not CONFIG.token:
        print("REMOTE_CODE_BRIDGE_TOKEN is required.", file=sys.stderr)
        print("Generate one with: openssl rand -hex 32", file=sys.stderr)
        return 2

    server = ThreadingHTTPServer((CONFIG.bind, CONFIG.port), BridgeHandler)
    print(f"{APP_NAME} listening on http://{CONFIG.bind}:{CONFIG.port}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
