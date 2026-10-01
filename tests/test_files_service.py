import plistlib
import subprocess
import sys

import pytest

from remote_code_bridge import BridgeError
from remote_code_bridge.files import path_block_for, replace_block, resolve_symlinks, write_private
from remote_code_bridge.service import ServiceManager, launchd_plist, systemd_unit, windows_task_xml

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlinks")


def test_replace_block_is_idempotent():
    once = replace_block("export A=1\n", "PATH", "line")
    assert once == "export A=1\n# >>> remote-code-bridge PATH >>>\nline\n# <<< remote-code-bridge PATH <<<\n"
    assert replace_block(once, "PATH", "line") == once
    assert replace_block(once, "PATH", "") == "export A=1\n"
    assert replace_block("Host a\n", "include", "Include x", at_top=True).startswith("# >>> remote-code-bridge")


@pytest.mark.parametrize(
    "shell,rc", [("/bin/zsh", ".zshrc"), ("/usr/bin/bash", ".bashrc"), ("/usr/bin/fish", ".config/fish/config.fish"),
                 ("/bin/sh", ".profile"), ("", ".profile")],
)  # fmt: skip
def test_path_block_for(shell, rc):
    assert path_block_for(shell)[0] == rc


@posix_only
def test_write_private(tmp_path):
    target = tmp_path / "dir" / "file"
    write_private(target, b"secret")
    assert target.read_bytes() == b"secret" and target.stat().st_mode & 0o777 == 0o600
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(BridgeError, match="symlink"):
        write_private(link, b"x")
    write_private(link, b"followed", follow_symlinks=True)
    assert target.read_bytes() == b"followed" and link.is_symlink()


@posix_only
def test_resolve_symlink_loop(tmp_path):
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    with pytest.raises(BridgeError, match="too many symlinks"):
        resolve_symlinks(tmp_path / "a")


def test_unit_files_run_the_installed_package():
    unit = systemd_unit("/opt/my python/100%/python3")
    assert 'ExecStart="/opt/my python/100%%/python3" "-m" "remote_code_bridge" "serve"' in unit
    assert "Restart=always" in unit
    plist = plistlib.loads(launchd_plist("/usr/bin/python3"))
    assert plist["ProgramArguments"] == ["/usr/bin/python3", "-m", "remote_code_bridge", "serve"]
    assert plist["KeepAlive"] is True


def test_windows_task_never_times_out_and_runs_on_battery():
    """B8 and B17: Scheduled Task defaults stop the bridge after 72h or when the laptop is unplugged."""
    xml = windows_task_xml(r"C:\Python\pythonw.exe", "PC\\me & co")
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "<Arguments>-m remote_code_bridge serve</Arguments>" in xml
    assert "PC\\me &amp; co" in xml


class Recorder:
    def __init__(self, fail=()):
        self.calls, self.fail = [], fail

    def __call__(self, argv):
        self.calls.append(argv)
        code = 1 if any(word in argv for word in self.fail) else 0
        return subprocess.CompletedProcess(argv, code, "", "boom" if code else "")


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_service_install_remove_and_stop(home, platform):
    if platform == "darwin" and sys.platform == "win32":
        pytest.skip("launchd domains use os.getuid(), which Windows lacks")
    run = Recorder()
    manager = ServiceManager(platform=platform, run=run, home=home)
    manager.stop()
    manager.install("/usr/bin/python3")
    expected = {"linux": "systemctl", "darwin": "launchctl", "win32": "schtasks"}[platform]
    assert expected in [argv[0] for argv in run.calls]
    unit = manager.unit_path()
    if unit is not None:
        assert unit.exists() and unit in manager.files()
    manager.remove()
    assert not any(path.exists() for path in manager.files())
    if platform == "win32":
        assert ["schtasks", "/Delete", "/TN", "remote-code-bridge", "/F"] in run.calls
    if platform == "linux":
        assert ["systemctl", "--user", "disable", "--now", "remote-code-bridge.service"] in run.calls


def test_service_failures_are_reported(home):
    manager = ServiceManager(platform="linux", run=Recorder(fail=("daemon-reload",)), home=home)
    with pytest.raises(BridgeError, match="daemon-reload"):
        manager.install("/usr/bin/python3")
    with pytest.raises(BridgeError, match="unsupported"):
        ServiceManager(platform="plan9", run=Recorder(), home=home).install("p")
