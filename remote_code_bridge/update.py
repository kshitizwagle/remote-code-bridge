"""`remote-code-bridge update`: upgrade the package, then re-run `install` so the remote gets the new
version too and the service (if you chose one) restarts on it.

The package is upgraded with whatever installed it: `uv tool` when this runs from a uv tool
environment, otherwise pip. RCB_PACKAGE_SPEC overrides where it comes from (for tests, or a fork).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from remote_code_bridge import BridgeError, is_valid_alias, ssh
from remote_code_bridge.config import config_dir, read_host_config
from remote_code_bridge.service import ServiceManager

PACKAGE = "git+https://github.com/kshitizwagle/remote-code-bridge"


def installed_with_uv() -> bool:
    return (Path(sys.prefix) / "uv-receipt.toml").exists()


def upgrade_command() -> list[str]:
    spec = os.environ.get("RCB_PACKAGE_SPEC") or PACKAGE
    if installed_with_uv():
        uv = shutil.which("uv")
        if not uv:
            raise BridgeError("this was installed with uv, but `uv` is not on PATH")
        return [uv, "tool", "install", "--force", "--reinstall", spec]
    return [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", spec]


def update(alias: str | None = None, services: ServiceManager | None = None) -> int:
    alias = alias or read_host_config(config_dir() / "host.env").default_host
    if not alias:
        raise BridgeError("could not determine SSH alias; run `remote-code-bridge update <ssh-alias>`")
    if not is_valid_alias(alias):
        raise BridgeError("SSH alias contains unsupported characters")
    upgrade = upgrade_command()
    reinstall = ["remote-code-bridge", "install", alias, "--yes"]
    # The service runs from the environment being replaced; stop it first (`install` restarts it).
    (services or ServiceManager()).stop()

    if sys.platform == "win32":
        return _update_after_exit(upgrade, reinstall)
    print(f"==> {' '.join(upgrade)}", file=sys.stderr)
    if subprocess.run(upgrade, env=ssh.child_env()).returncode != 0:
        raise BridgeError(
            "upgrading the package failed (see above); the bridge is stopped until you run "
            "`remote-code-bridge install` (or retry the update)"
        )
    # Run the *new* version's installer, from the freshly installed command.
    command = shutil.which("remote-code-bridge") or reinstall[0]
    result = subprocess.run([command, *reinstall[1:]], env=ssh.child_env())
    if result.returncode == 0:
        print(f"remote-code-bridge update: updated for SSH alias {alias}")
    return result.returncode


def _update_after_exit(upgrade: list[str], reinstall: list[str]) -> int:
    """Windows can't replace a running python.exe, and this process is one. Hand the work to a
    small script that waits for us to exit, in its own console window so you can watch it."""

    def quote(argument: str) -> str:
        return subprocess.list2cmdline([argument])

    script = Path(tempfile.mkdtemp(prefix="remote-code-bridge-update-")) / "update.cmd"
    script.write_text(
        "@echo off\r\n"
        "echo Updating remote-code-bridge...\r\n"
        "ping -n 3 127.0.0.1 >nul\r\n"
        f"{' '.join(quote(part) for part in upgrade)} || goto failed\r\n"
        f"{' '.join(quote(part) for part in reinstall)} || goto failed\r\n"
        "echo Done.\r\n"
        "pause\r\nexit /b 0\r\n"
        ":failed\r\necho Update failed; see the messages above.\r\npause\r\nexit /b 1\r\n",
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", "start", "remote-code-bridge update", "cmd.exe", "/c", str(script)],
        env=ssh.child_env(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print("remote-code-bridge update: continuing in a new window once this command exits")
    return 0
