"""Command-line entry point. Invoked as `code`, it behaves like `remote-code-bridge open`."""

from __future__ import annotations

import os
import sys

from remote_code_bridge import BridgeError, __version__, generate_token
from remote_code_bridge.config import (
    RemoteConfig,
    host_config_path,
    log_path,
    read_host_config,
    read_remote_config,
    remote_config_path,
)

USAGE = """usage: remote-code-bridge <command>

  on the host:    install [ssh-alias] [--service | --no-service] [--yes]
                  serve                      run the bridge (if you didn't choose the login service)
                  update [ssh-alias]         upgrade the package and the remote
  on the remote:  open [flags] [path]        (also runs as `code`)
  either side:    status | uninstall [--yes] [--host-only] | generate-token | --version"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if os.path.basename(argv[0]).lower() in ("code", "code.exe"):
        command, rest = "open", argv[1:]
    else:
        command, rest = (argv[1], argv[2:]) if len(argv) > 1 else ("help", [])
    try:
        return run(command, rest)
    except BridgeError as error:
        print(f"remote-code-bridge: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def run(command: str, rest: list[str]) -> int:
    if command == "open":
        from remote_code_bridge.client import open_in_vscode

        print(open_in_vscode(read_remote_config(remote_config_path()), rest))
        return 0
    if command == "serve":
        _no_arguments(command, rest)
        from remote_code_bridge import log
        from remote_code_bridge.server import serve

        config = read_host_config(host_config_path())
        log.setup(log_path(), config.token)
        serve(config)
        return 0
    if command == "status":
        _no_arguments(command, rest)
        return status()
    if command == "install":
        flags = [arg for arg in rest if arg.startswith("-")]
        aliases = [arg for arg in rest if not arg.startswith("-")]
        if set(flags) - {"--yes", "-y", "--service", "--no-service"} or len(aliases) > 1:
            raise BridgeError("usage: remote-code-bridge install [ssh-alias] [--service | --no-service] [--yes]")
        if {"--service", "--no-service"} <= set(flags):
            raise BridgeError("choose either --service or --no-service")
        service = True if "--service" in flags else False if "--no-service" in flags else None
        from remote_code_bridge.install import install

        install(aliases[0] if aliases else None, assume_yes=bool({"--yes", "-y"} & set(flags)), service=service)
        return 0
    if command == "uninstall":
        if set(rest) - {"--yes", "-y", "--host-only"}:
            raise BridgeError("usage: remote-code-bridge uninstall [--yes] [--host-only]")
        from remote_code_bridge.uninstall import uninstall

        uninstall(assume_yes=bool({"--yes", "-y"} & set(rest)), host_only="--host-only" in rest)
        return 0
    if command == "update":
        if len(rest) > 1:
            raise BridgeError("usage: remote-code-bridge update [ssh-alias]")
        from remote_code_bridge.update import update

        return update(rest[0] if rest else None)
    if command == "generate-token":
        _no_arguments(command, rest)
        print(generate_token())
        return 0
    if command in ("--version", "version"):
        print(f"remote-code-bridge {__version__}")
        return 0
    if command in ("help", "--help", "-h"):
        print(USAGE)
        return 0
    raise BridgeError(USAGE)


def _no_arguments(command: str, rest: list[str]) -> None:
    if rest:
        raise BridgeError(f"{command} does not accept arguments")


def status() -> int:
    """Check whichever side(s) are installed here. Exit 0 if healthy, 1 if not."""
    checked, healthy = False, True
    if host_config_path().exists():
        checked = True
        healthy &= _host_status()
    if remote_config_path().exists():
        checked = True
        healthy &= _remote_status()
    if not checked:
        raise BridgeError("not installed here (no host.env or remote.env in ~/.config/remote-code-bridge)")
    return 0 if healthy else 1


def _host_status() -> bool:
    from remote_code_bridge.client import call

    config = read_host_config(host_config_path())
    try:
        health = call(RemoteConfig(port=config.port), "GET", "/healthz")
    except BridgeError as error:
        print(f"host bridge: not running on 127.0.0.1:{config.port} ({error})")
        return False
    tunnel = health.get("tunnel") or {}
    state = tunnel.get("state", "unknown")
    print(f"host bridge: running, version {health.get('version', '?')}, 127.0.0.1:{config.port}")
    line = f"tunnel:      {state}"
    if tunnel.get("alias"):
        line += f" to {tunnel['alias']}"
    if tunnel.get("since"):
        line += f" since {tunnel['since']}"
    print(line)
    if tunnel.get("last_error") and state != "up":
        print(f"last error:  {tunnel['last_error']}")
    if state == "auth_failed":
        print("fix:         the service needs SSH login without a prompt; load your key into ssh-agent")
    return state in ("up", "disabled")


def _remote_status() -> bool:
    from remote_code_bridge.client import call

    config = read_remote_config(remote_config_path())
    where = config.socket or f"127.0.0.1:{config.port}"
    try:
        health = call(config, "GET", "/healthz")
        check = call(config, "POST", "/check", b"{}", config.token or "")
    except BridgeError as error:
        print(f"tunnel:      not connected at {where}")
        print(f"             {error}")
        return False
    print(f"tunnel:      connected to host bridge {health.get('version', '?')} at {where}")
    if not check.get("ok"):
        print(f"token:       rejected ({check.get('error')}); reinstall from the host")
        return False
    print(f"token:       accepted; VS Code will open ssh-remote+{config.host_alias}")
    return True
