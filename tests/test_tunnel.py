import subprocess
import sys

import pytest

from remote_code_bridge import tunnel
from remote_code_bridge.tunnel import TunnelSupervisor, connect_argv, kill_orphan, prep_command

SOCKET = "/home/u/.cache/remote-code-bridge/bridge.sock"


class Sleeps:
    """Replaces the backoff wait: records each delay and asks the loop to stop after `limit` waits."""

    def __init__(self, limit):
        self.delays, self.limit = [], limit

    def __call__(self, seconds):
        self.delays.append(seconds)
        return len(self.delays) >= self.limit


def supervisor(home, sleeps):
    return TunnelSupervisor("devbox", SOCKET, 39731, pid_file=home / "tunnel.pid", sleep=sleeps)


def test_prep_clears_a_stale_socket_in_a_private_directory():
    command = prep_command("/home/my user/s.sock")
    assert command.startswith("sh -c ")
    assert "chmod 700" in command and "rm -f" in command and "'\"'\"'/home/my user/s.sock'\"'\"'" in command


def test_connect_argv_keeps_the_forward_and_skips_multiplexing(fake_ssh):
    argv = connect_argv("devbox", SOCKET, 4000)
    assert argv[-3:] == ["-R", f"{SOCKET}:127.0.0.1:4000", "devbox"]
    assert "ExitOnForwardFailure=yes" in argv and "ControlPath=none" in argv and "BatchMode=yes" in argv
    # ClearAllForwardings would also clear our own -R
    assert "ClearAllForwardings=yes" not in argv


def test_backoff_doubles_up_to_the_cap(home, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_TUNNEL", "exit:255:Error: remote port forwarding failed for listen path")
    monkeypatch.setattr(tunnel, "MAX_DELAY", 4.0)
    sleeps = Sleeps(limit=5)
    sup = supervisor(home, sleeps)
    sup.run()
    assert [round(delay / base, 1) <= 1.2 and round(delay / base, 1) >= 0.8
            for delay, base in zip(sleeps.delays, [1, 2, 4, 4, 4])] == [True] * 5  # fmt: skip
    assert sup.status()["state"] == "stopped"
    assert sup.status()["last_error"] == "Error: remote port forwarding failed for listen path"


def test_prep_runs_before_every_connect(home, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_TUNNEL", "exit:255:boom")
    supervisor(home, Sleeps(limit=2)).run()
    kinds = ["connect" if "-N" in call["argv"] else "prep" for call in fake_ssh()]
    assert kinds == ["prep", "connect", "prep", "connect"]
    assert "rm -f" in fake_ssh()[0]["command"]
    assert "ClearAllForwardings=yes" in fake_ssh()[0]["argv"]


def test_prep_failure_skips_connect(home, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_PREP", "fail:ssh: connect to host devbox port 22: Network is unreachable")
    sleeps = Sleeps(limit=1)
    sup = supervisor(home, sleeps)
    sup.run()
    assert all("-N" not in call["argv"] for call in fake_ssh())
    assert "Network is unreachable" in sup.status()["last_error"]


def test_auth_failure_waits_the_maximum(home, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_TUNNEL", "exit:255:devbox: Permission denied (publickey).")
    states = []
    sleeps = Sleeps(limit=1)
    sup = supervisor(home, lambda s: states.append(sup.status()) or sleeps(s))
    sup.run()
    assert states[0]["state"] == "auth_failed"
    assert states[0]["retry_in"] == tunnel.MAX_DELAY


def test_stable_connection_resets_backoff(home, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_TUNNEL", "after:0.3:255:Connection reset")
    monkeypatch.setattr(tunnel, "STABLE_AFTER", 0.1)
    sleeps = Sleeps(limit=3)
    supervisor(home, sleeps).run()
    assert all(delay <= 1.2 for delay in sleeps.delays)


def test_tunnel_comes_up_and_stops_cleanly(home, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_TUNNEL", "stay")
    monkeypatch.setattr(tunnel, "UP_AFTER", 0.2)
    sup = TunnelSupervisor("devbox", SOCKET, 39731, pid_file=home / "tunnel.pid")
    sup.start()
    import time

    deadline = time.monotonic() + 15
    while sup.status()["state"] != "up":
        assert time.monotonic() < deadline, sup.status()
        time.sleep(0.05)
    assert (home / "tunnel.pid").exists()
    assert sup.status()["last_error"] is None
    sup.stop()
    assert sup.status()["state"] == "stopped"
    assert not (home / "tunnel.pid").exists()


def test_kill_orphan_only_kills_ssh(home):
    pid_file = home / "tunnel.pid"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        pid_file.write_text(str(child.pid))
        kill_orphan(pid_file)  # it's python, not ssh: left alone
        assert child.poll() is None
        assert not pid_file.exists()
    finally:
        child.kill()
        child.wait()
    pid_file.write_text("garbage")
    kill_orphan(pid_file)
    kill_orphan(home / "missing.pid")


@pytest.mark.skipif(sys.platform == "win32", reason="uses /bin/sleep renamed to ssh")
def test_kill_orphan_stops_a_leftover_ssh(home, tmp_path):
    import shutil

    fake = tmp_path / "ssh"
    shutil.copy(shutil.which("sleep"), fake)
    child = subprocess.Popen([str(fake), "30"])
    try:
        (home / "tunnel.pid").write_text(str(child.pid))
        kill_orphan(home / "tunnel.pid")
        assert child.wait(timeout=5) != 0
    finally:
        if child.poll() is None:
            child.kill()


def test_process_name_of_self():
    import os

    assert tunnel.process_name(os.getpid())
