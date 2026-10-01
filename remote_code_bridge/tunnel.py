"""Keeps exactly one reverse tunnel from the remote back to this host's bridge.

The tunnel belongs to the host service, not to your terminal sessions, so `code .` works in every
SSH session on the remote, however many you open and in whatever order you close them.

    PREP     ssh <alias> 'mkdir -p <dir>; chmod 700 <dir>; rm -f <socket>'   (clear a stale socket)
    CONNECT  ssh -N -R <socket>:127.0.0.1:<port> <alias>                      (runs until it dies)
    BACKOFF  1s, 2s, 4s ... 60s, then PREP again; reset after a connection stays up for 60s
"""

from __future__ import annotations

import collections
import datetime
import logging
import os
import posixpath
import random
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from remote_code_bridge import ssh
from remote_code_bridge.config import state_dir

log = logging.getLogger(__name__)

UP_AFTER = 3.0  # seconds alive before we call the tunnel "up"
STABLE_AFTER = 60.0  # seconds alive before the backoff delay resets
MIN_DELAY, MAX_DELAY = 1.0, 60.0
AUTH_ERRORS = ("Permission denied", "Host key verification failed")
CONNECT_OPTIONS = [
    "-N", "-T",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
]  # fmt: skip


def prep_command(remote_socket: str) -> str:
    directory = posixpath.dirname(remote_socket)
    quoted_dir, quoted_socket = shlex.quote(directory), shlex.quote(remote_socket)
    return ssh.sh(
        f"umask 077; mkdir -p {quoted_dir} && chmod 700 {quoted_dir} && [ -O {quoted_dir} ] && rm -f {quoted_socket}"
    )


def connect_argv(alias: str, remote_socket: str, local_port: int) -> list:
    # No ssh.NO_FORWARDS here: ClearAllForwardings would also drop our own -R.
    return (
        ssh.ssh_program()
        + ssh.NO_MULTIPLEX
        + ssh.BATCH
        + CONNECT_OPTIONS
        + ["-R", f"{remote_socket}:127.0.0.1:{local_port}", alias]
    )


class TunnelSupervisor:
    def __init__(
        self,
        alias: str,
        remote_socket: str,
        local_port: int,
        pid_file: Path | None = None,
        sleep: Callable[[float], bool] | None = None,
    ):
        self.alias = alias
        self.remote_socket = remote_socket
        self.local_port = local_port
        self.pid_file = pid_file or state_dir() / "tunnel.pid"
        self._stop = threading.Event()
        # Returns True if we should stop. Tests replace it to skip real waiting.
        self._sleep = sleep or self._stop.wait
        self._lock = threading.Lock()
        self._child: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {"state": "connecting", "alias": alias, "since": _now(), "last_error": None}

    # -- status ---------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _set(self, state: str, **extra: Any) -> None:
        with self._lock:
            if self._status["state"] != state:
                log.info("tunnel %s", state)
            self._status = {
                "state": state,
                "alias": self.alias,
                "since": _now(),
                "last_error": extra.pop("last_error", self._status.get("last_error")),
                **extra,
            }

    # -- lifecycle ------------------------------------------------------------------------------

    def start(self) -> None:
        kill_orphan(self.pid_file)
        self._thread = threading.Thread(target=self.run, name="tunnel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            child = self._child
        if child is not None:
            _terminate(child)
        if self._thread is not None:
            self._thread.join(timeout=10)

    def run(self) -> None:
        delay = MIN_DELAY
        while not self._stop.is_set():
            self._set("connecting")
            uptime, error = self._attempt()
            if self._stop.is_set():
                break
            if uptime >= STABLE_AFTER:
                delay = MIN_DELAY
            if any(marker in error for marker in AUTH_ERRORS):
                delay = MAX_DELAY
                self._set("auth_failed", last_error=error, retry_in=delay)
            else:
                self._set("backoff", last_error=error or "tunnel closed", retry_in=delay)
            log.warning("tunnel to %s down (%s); retrying in %.0fs", self.alias, error or "closed", delay)
            if self._sleep(delay * random.uniform(0.8, 1.2)):
                break
            delay = min(delay * 2, MAX_DELAY)
        self._set("stopped")

    def _attempt(self) -> tuple[float, str]:
        """One PREP + CONNECT. Returns (seconds the tunnel stayed up, last error line)."""
        prep = ssh.run(self.alias, prep_command(self.remote_socket), options=["-o", "ConnectTimeout=10"], timeout=30)
        if prep.returncode != 0:
            return 0.0, ssh.describe_failure(prep)

        stderr: collections.deque[str] = collections.deque(maxlen=20)
        try:
            child = subprocess.Popen(
                connect_argv(self.alias, self.remote_socket, self.local_port),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=ssh.child_env(),
                creationflags=ssh.CREATION_FLAGS,
                preexec_fn=_die_with_parent if sys.platform.startswith("linux") else None,
            )
        except OSError as error:
            return 0.0, f"could not start ssh: {error}"
        with self._lock:
            self._child = child
        _write_pid(self.pid_file, child.pid)
        reader = threading.Thread(target=_drain, args=(child, stderr), daemon=True)
        reader.start()

        started = time.monotonic()
        while True:
            try:
                child.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if self._stop.is_set():
                    _terminate(child)
                    break
                if time.monotonic() - started >= UP_AFTER and self.status()["state"] != "up":
                    self._set("up", last_error=None)
        uptime = time.monotonic() - started
        reader.join(timeout=2)
        with self._lock:
            self._child = None
        _remove_pid(self.pid_file, child.pid)
        return uptime, stderr[-1] if stderr else f"ssh exited with status {child.returncode}"


# -- process helpers ------------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _drain(child: subprocess.Popen, lines: collections.deque[str]) -> None:
    assert child.stderr is not None
    for raw in child.stderr:
        line = raw.decode("utf-8", "replace").strip()
        if line:
            lines.append(line[:200])
            log.info("ssh: %s", line[:200])


def _terminate(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()


def _die_with_parent() -> None:
    """Linux only: if the service dies without cleaning up, the kernel stops ssh too."""
    import ctypes

    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
    except OSError:
        pass


def _write_pid(pid_file: Path, pid: int) -> None:
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(pid), encoding="ascii")
    except OSError as error:
        log.warning("could not write %s: %s", pid_file, error)


def _remove_pid(pid_file: Path, pid: int) -> None:
    try:
        if pid_file.read_text(encoding="ascii").strip() == str(pid):
            pid_file.unlink()
    except (OSError, ValueError):
        pass


def kill_orphan(pid_file: Path) -> None:
    """Stop an ssh tunnel left behind by a service that crashed or was killed."""
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return
    if process_name(pid) in ("ssh", "ssh.exe"):
        log.info("stopping leftover tunnel process %d", pid)
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=ssh.CREATION_FLAGS
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        pid_file.unlink()
    except OSError:
        pass


def process_name(pid: int) -> str | None:
    """The executable name of a running process, or None. Used to avoid killing a reused PID."""
    try:
        if sys.platform.startswith("linux"):
            return Path(f"/proc/{pid}/comm").read_text().strip()
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, creationflags=ssh.CREATION_FLAGS,
            ).stdout  # fmt: skip
            return out.split(",")[0].strip('"').lower() if out.startswith('"') else None
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip()
        return os.path.basename(out) or None
    except OSError:
        return None
