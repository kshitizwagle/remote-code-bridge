"""`remote-code-bridge uninstall`: remove everything installation added, on this machine and the remote.

It undoes exactly what the manifest (manifest.py) recorded: files and directories it created, lines it
added to shell startup files, and the login service. Anything that existed before is left alone.
On the host it first asks the remote to uninstall itself over SSH, so no trace is left there either.

The program itself (installed with uv or pip) is the package manager's to remove; the last message
says how. Everything used below is imported up front, because on the remote this deletes the very
file it is running from.
"""

from __future__ import annotations

import sys
from pathlib import Path

from remote_code_bridge import BridgeError, ssh
from remote_code_bridge.client import call
from remote_code_bridge.config import RemoteConfig, config_dir, home_dir, log_path, read_host_config, state_dir
from remote_code_bridge.files import path_block_for
from remote_code_bridge.manifest import Manifest
from remote_code_bridge.service import ServiceManager


def fallback_manifest(home: Path) -> Manifest:
    """Without a manifest (an install from before manifests existed), remove what we know we create.
    Directories that may have existed before, like ~/.local/bin, are left alone."""
    manifest = Manifest(config_dir() / "manifest.json", home)
    manifest.owned_dirs = [str(config_dir()), str(state_dir()), str(home / ".cache" / "remote-code-bridge")]
    manifest.files = [str(log_path())]
    bin_dir = home / ".local" / "bin"
    code = bin_dir / "code"
    if (bin_dir / "remote-code-bridge").is_file() and _is_our_archive(bin_dir / "remote-code-bridge"):
        manifest.files.append(str(bin_dir / "remote-code-bridge"))  # the remote copy, not a uv/pip command
        if code.is_symlink():
            manifest.files.append(str(code))
    for shell in ("bash", "zsh", "fish", "sh"):
        manifest.blocks.append({"path": str(home / path_block_for(shell)[0]), "name": "PATH", "created_file": False})
    host_env = config_dir() / "host.env"
    if host_env.exists():
        manifest.service = sys.platform
        for line in host_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("REMOTE_CODE_BRIDGE_TUNNEL_ALIAS="):
                manifest.remote_alias = line.split("=", 1)[1].strip() or None
    return manifest


def _is_our_archive(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            head = stream.read(64)
    except OSError:
        return False
    return head.startswith(b"#!/usr/bin/env python3") and path.stat().st_size > 1000


def uninstall(assume_yes: bool = False, host_only: bool = False, services: ServiceManager | None = None) -> None:
    home = home_dir()
    manifest = Manifest.load(config_dir() / "manifest.json", home)
    if not manifest.exists():
        manifest = fallback_manifest(home)
    services = services or ServiceManager()

    plan = manifest.describe()
    if manifest.remote_alias and not host_only:
        plan.insert(0, f"uninstall from {manifest.remote_alias} (over SSH)")
    if not plan:
        print("remote-code-bridge: nothing to uninstall here")
        return
    print("This will:")
    for line in plan:
        print(f"  - {line}")
    if not assume_yes:
        if not sys.stdin.isatty():
            raise BridgeError("pass --yes to uninstall without a prompt")
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            raise BridgeError("cancelled")

    # The tunnel must be down while the remote is cleaned, or it would recreate the socket directory.
    if manifest.service:
        services.stop()
    elif manifest.remote_alias and bridge_is_running():
        raise BridgeError("stop `remote-code-bridge serve` first (it keeps a tunnel open to the remote)")
    if manifest.remote_alias and not host_only:
        try:
            uninstall_remote(manifest.remote_alias)
        except BridgeError:
            if manifest.service:  # nothing was removed; put things back as they were
                services.install(sys.executable)
            raise
    if manifest.service:
        services.remove()
    problems = manifest.undo()
    for problem in problems:
        print(f"remote-code-bridge: {problem}", file=sys.stderr)
    if problems:
        raise BridgeError("uninstall finished with problems (listed above)")
    print("remote-code-bridge: uninstalled.")
    if manifest.remote_alias:
        print("To remove the program itself: uv tool uninstall remote-code-bridge")
        print("                     (or, if you used pip: pip uninstall remote-code-bridge)")


def bridge_is_running() -> bool:
    try:
        port = read_host_config(config_dir() / "host.env").port
        call(RemoteConfig(port=port), "GET", "/healthz")
        return True
    except BridgeError:
        return False


def uninstall_remote(alias: str) -> None:
    result = ssh.run(alias, ssh.sh('p="$HOME/.local/bin/remote-code-bridge"; [ ! -e "$p" ] || "$p" uninstall --yes'))
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).decode("utf-8", "replace").strip() or ssh.describe_failure(result)
        raise BridgeError(
            f"could not uninstall on {alias}:\n{detail}\n"
            "Fix SSH access and run this again, or pass --host-only to clean only this machine "
            "(then run `~/.local/bin/remote-code-bridge uninstall` on the remote yourself)."
        )
    output = result.stdout.decode("utf-8", "replace").strip()
    if output:
        print("\n".join(f"  {alias}: {line}" for line in output.splitlines()))
