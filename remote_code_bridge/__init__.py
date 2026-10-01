"""remote-code-bridge: run `code .` on an SSH remote and open VS Code on your own machine."""

from __future__ import annotations

import re
import secrets

__version__ = "2.0.0"

APP_NAME = "remote-code-bridge"
DEFAULT_PORT = 39731
MAX_REQUEST_BYTES = 64 * 1024

# Environment variables that must never reach a child process (VS Code, ssh, installers).
SECRET_ENV = ("REMOTE_CODE_BRIDGE_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "token")

_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TOKEN = re.compile(r"[0-9A-Fa-f]{64}")


class BridgeError(Exception):
    """An error with a message meant for the user; the CLI prints it and exits 2."""


def is_valid_alias(alias: str) -> bool:
    """A concrete SSH alias: no wildcards, no leading dash (so it can't become an ssh option)."""
    return bool(_ALIAS.fullmatch(alias))


def is_valid_token(token: str) -> bool:
    return bool(_TOKEN.fullmatch(token))


def generate_token() -> str:
    return secrets.token_hex(32)
