"""`remote-code-bridge update`: upgrade the package, then re-run `install` so the remote gets the new
version too and the service (if you chose one) restarts on it.

It installs the newest release from PyPI and does nothing when that is the running version. The
package is upgraded with whatever installed it: `uv tool` when this runs from a uv tool environment,
otherwise pip. An installation from the GitHub URL moves to PyPI on its first update.
RCB_PACKAGE_SPEC overrides where it comes from and skips the lookup (for tests, a fork, or a local
checkout).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from remote_code_bridge import APP_NAME, BridgeError, __version__, is_valid_alias, ssh
from remote_code_bridge.config import config_dir, read_host_config
from remote_code_bridge.service import ServiceManager

PACKAGE = APP_NAME
LATEST_RELEASE_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
RELEASE_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")


def latest_release() -> str:
    """Ask PyPI for the newest release and return its version, for example `2.1.0`."""
    request = urllib.request.Request(
        LATEST_RELEASE_URL, headers={"Accept": "application/json", "User-Agent": f"{APP_NAME}/{__version__}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise BridgeError("no release has been published on PyPI yet") from error
        raise BridgeError(f"could not look up the latest release: PyPI answered HTTP {error.code}") from error
    except (OSError, ValueError) as error:  # URLError is an OSError; bad JSON is a ValueError
        raise BridgeError(f"could not look up the latest release: {error}") from error
    info = payload.get("info") if isinstance(payload, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    if not isinstance(version, str) or not RELEASE_VERSION.fullmatch(version):
        raise BridgeError("PyPI returned a release without a usable version")
    return version


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def installed_with_uv() -> bool:
    return (Path(sys.prefix) / "uv-receipt.toml").exists()


def upgrade_command(spec: str) -> list[str]:
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
    spec = os.environ.get("RCB_PACKAGE_SPEC")
    if not spec:
        latest = latest_release()
        if version_key(latest) <= version_key(__version__):
            print(f"remote-code-bridge update: already on the latest release ({__version__})")
            return 0
        print(f"==> updating {__version__} to {latest}", file=sys.stderr)
        # A lower bound, not ==: uv keeps the constraint, and a pin would hold back `uv tool upgrade`.
        spec = f"{PACKAGE}>={latest}"
    upgrade = upgrade_command(spec)
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
        # list2cmdline only quotes whitespace; cmd.exe would read the > in "pkg>=1.0" as a redirect.
        if set("<>&|^").intersection(argument):
            return f'"{argument}"'
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
