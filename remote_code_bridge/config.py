"""Configuration: KEY=VALUE files, overridden by non-empty environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from remote_code_bridge import DEFAULT_PORT, BridgeError, is_valid_token

Environment = Callable[[str], Optional[str]]


def home_dir() -> Path:
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if not home:
        raise BridgeError("HOME/USERPROFILE is not set")
    return Path(home)


def config_dir() -> Path:
    return home_dir() / ".config" / "remote-code-bridge"


def state_dir() -> Path:
    """Where logs and the tunnel PID file live."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "remote-code-bridge" if base else config_dir()
    xdg = os.environ.get("XDG_STATE_HOME")
    return (Path(xdg) if xdg else home_dir() / ".local" / "state") / "remote-code-bridge"


def log_path() -> Path:
    if sys.platform == "darwin":
        return home_dir() / "Library" / "Logs" / "remote-code-bridge.log"
    return state_dir() / "bridge.log"


@dataclass(frozen=True)
class HostConfig:
    bind: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    token: str | None = field(default=None, repr=False)
    code_bin: str = "code"
    default_host: str | None = None
    allowed_hosts: frozenset[str] | None = None
    dry_run: bool = False
    tunnel_enabled: bool = True
    tunnel_alias: str | None = None
    tunnel_socket: str | None = None


@dataclass(frozen=True)
class RemoteConfig:
    port: int = DEFAULT_PORT
    host_alias: str | None = None
    token: str | None = field(default=None, repr=False)
    socket: str | None = None


def process_environment(key: str) -> str | None:
    return os.environ.get(key) or None


def read_values(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file. A missing file is the same as an empty one."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise BridgeError(f"could not read {path}: {error}") from error
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def update_env_text(old_text: str, values: Mapping[str, str]) -> str:
    """Replace managed keys in an env file, keeping comments and unknown keys in place."""
    lines = []
    for line in old_text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key not in values:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in values.items())
    return "\n".join(lines) + "\n"


class _Lookup:
    """Environment first (when non-empty), then the file."""

    def __init__(self, values: Mapping[str, str], environment: Environment):
        self.values = values
        self.environment = environment

    def __call__(self, key: str) -> str | None:
        return self.environment(key) or self.values.get(key) or None


def _port(raw: str | None) -> int:
    try:
        port = int(raw) if raw is not None else DEFAULT_PORT
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise BridgeError("REMOTE_CODE_BRIDGE_PORT must be an integer between 1 and 65535")
    return port


def _token(raw: str | None) -> str | None:
    if raw is not None and not is_valid_token(raw):
        raise BridgeError("REMOTE_CODE_BRIDGE_TOKEN must be exactly 64 ASCII hex characters")
    return raw


def _flag(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes")


def read_host_config(path: Path, environment: Environment = process_environment) -> HostConfig:
    get = _Lookup(read_values(path), environment)
    bind = get("REMOTE_CODE_BRIDGE_BIND") or "127.0.0.1"
    if bind != "127.0.0.1":
        raise BridgeError("REMOTE_CODE_BRIDGE_BIND must be 127.0.0.1")
    default_host = get("REMOTE_CODE_BRIDGE_DEFAULT_HOST")
    allowed = frozenset(
        host.strip() for host in (get("REMOTE_CODE_BRIDGE_ALLOWED_HOSTS") or "").split(",") if host.strip()
    )
    if not allowed and default_host:
        allowed = frozenset([default_host])
    return HostConfig(
        bind=bind,
        port=_port(get("REMOTE_CODE_BRIDGE_PORT")),
        token=_token(get("REMOTE_CODE_BRIDGE_TOKEN")),
        code_bin=get("REMOTE_CODE_BRIDGE_CODE_BIN") or "code",
        default_host=default_host,
        allowed_hosts=allowed or None,
        dry_run=_flag(get("REMOTE_CODE_BRIDGE_DRY_RUN"), False),
        tunnel_enabled=_flag(get("REMOTE_CODE_BRIDGE_TUNNEL"), True),
        tunnel_alias=get("REMOTE_CODE_BRIDGE_TUNNEL_ALIAS") or default_host,
        tunnel_socket=get("REMOTE_CODE_BRIDGE_TUNNEL_SOCKET"),
    )


def read_remote_config(path: Path, environment: Environment = process_environment) -> RemoteConfig:
    get = _Lookup(read_values(path), environment)
    return RemoteConfig(
        port=_port(get("REMOTE_CODE_BRIDGE_PORT")),
        host_alias=get("REMOTE_CODE_BRIDGE_HOST_ALIAS"),
        token=_token(get("REMOTE_CODE_BRIDGE_TOKEN")),
        socket=get("REMOTE_CODE_BRIDGE_SOCKET"),
    )


def host_config_path() -> Path:
    return config_dir() / "host.env"


def remote_config_path() -> Path:
    override = os.environ.get("REMOTE_CODE_BRIDGE_CONFIG")
    return Path(override) if override else config_dir() / "remote.env"
