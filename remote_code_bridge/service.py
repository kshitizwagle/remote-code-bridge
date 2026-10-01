"""Run `remote-code-bridge serve` at login: systemd (Linux), launchd (macOS), Scheduled Task (Windows)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List
from xml.sax.saxutils import escape

from remote_code_bridge import BridgeError
from remote_code_bridge.config import home_dir
from remote_code_bridge.files import write_private
from remote_code_bridge.ssh import CREATION_FLAGS

UNIT = "remote-code-bridge.service"
LABEL = "com.remote-code-bridge"
TASK = "remote-code-bridge"

Runner = Callable[[List[str]], subprocess.CompletedProcess]


def run_command(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, creationflags=CREATION_FLAGS)
    except FileNotFoundError as error:
        raise BridgeError(f"{argv[0]} is required to run the host service") from error


def serve_command(python: str) -> list[str]:
    """How the service starts the bridge: the installed package, run by its own Python."""
    return [python, "-m", "remote_code_bridge", "serve"]


def systemd_unit(python: str) -> str:
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    command = " ".join(quote(part) for part in serve_command(python))
    return (
        "[Unit]\nDescription=remote-code-bridge host service (opens VS Code, keeps the SSH tunnel)\n"
        "After=network-online.target\n\n"
        f"[Service]\nType=simple\nExecStart={command}\n"
        "Restart=always\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
    )


def launchd_plist(python: str) -> bytes:
    return plistlib.dumps(
        {"Label": LABEL, "ProgramArguments": serve_command(python), "RunAtLoad": True, "KeepAlive": True}
    )


def windows_task_xml(pythonw: str, user: str) -> str:
    # ExecutionTimeLimit PT0S: the default (72 hours) would kill the bridge after three days.
    # Battery settings: the defaults would refuse to start, or stop it, on an unplugged laptop.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>remote-code-bridge host service</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(pythonw)}</Command><Arguments>-m remote_code_bridge serve</Arguments></Exec>
  </Actions>
</Task>
"""


def windowless_python(python: str) -> str:
    """pythonw.exe runs without opening a console window."""
    candidate = Path(python).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else python


class ServiceManager:
    def __init__(self, platform: str = sys.platform, run: Runner = run_command, home: Path | None = None):
        self.platform = platform
        self.run = run
        self.home = home or home_dir()

    def _must(self, argv: list[str]) -> None:
        result = self.run(argv)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise BridgeError(f"`{' '.join(argv)}` failed: {detail}")

    def stop(self) -> None:
        """Stop a running bridge (v1 or v2) so its files can be replaced. Never fails."""
        if self.platform.startswith("linux"):
            self.run(["systemctl", "--user", "stop", UNIT])
        elif self.platform == "darwin":
            self.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        elif self.platform == "win32":
            self.run(["schtasks", "/End", "/TN", TASK])
            self.run(["taskkill", "/IM", "remote-code-bridge.exe", "/F"])  # v1 binary

    def unit_path(self) -> Path | None:
        if self.platform.startswith("linux"):
            return self.home / ".config" / "systemd" / "user" / UNIT
        if self.platform == "darwin":
            return self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        return None

    def files(self) -> list[Path]:
        """Every file registering the service creates (for the uninstall manifest)."""
        unit = self.unit_path()
        if unit is None:
            return []
        if self.platform.startswith("linux"):  # `systemctl enable` adds this link
            return [unit, unit.parent / "default.target.wants" / UNIT]
        return [unit]

    def install(self, python: str) -> None:
        unit = self.unit_path()
        if self.platform.startswith("linux"):
            assert unit is not None
            write_private(unit, systemd_unit(python).encode())
            self._must(["systemctl", "--user", "daemon-reload"])
            self._must(["systemctl", "--user", "enable", UNIT])
            self._must(["systemctl", "--user", "restart", UNIT])
        elif self.platform == "darwin":
            assert unit is not None
            write_private(unit, launchd_plist(python))
            domain = f"gui/{os.getuid()}"
            self.run(["launchctl", "bootout", f"{domain}/{LABEL}"])
            self._must(["launchctl", "bootstrap", domain, str(unit)])
            self._must(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"])
        elif self.platform == "win32":
            user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
            xml = windows_task_xml(windowless_python(python), user)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "task.xml"
                path.write_bytes(xml.encode("utf-16"))
                self._must(["schtasks", "/Create", "/TN", TASK, "/XML", str(path), "/F"])
            self._must(["schtasks", "/Run", "/TN", TASK])
        else:
            raise BridgeError(f"unsupported host platform: {self.platform}")

    def remove(self) -> None:
        """Stop and unregister the service and delete its files. Never fails."""
        unit = self.unit_path()
        if self.platform.startswith("linux"):
            self.run(["systemctl", "--user", "disable", "--now", UNIT])
        elif self.platform == "darwin":
            self.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        elif self.platform == "win32":
            self.run(["schtasks", "/End", "/TN", TASK])
            self.run(["schtasks", "/Delete", "/TN", TASK, "/F"])
        for path in self.files():
            if os.path.lexists(path):
                os.unlink(path)
        if self.platform.startswith("linux") and unit is not None:
            self.run(["systemctl", "--user", "daemon-reload"])
