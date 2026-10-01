"""Running the user's own `ssh` client. Every call goes through here."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Sequence

from remote_code_bridge import SECRET_ENV, BridgeError

# Never share a connection with (or request forwards from) the user's interactive sessions.
NO_MULTIPLEX = ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
# Ignore RemoteForward/LocalForward lines in the user's config for helper connections. This also
# clears forwards given on the command line, so the tunnel connection itself must not use it.
NO_FORWARDS = ["-o", "ClearAllForwardings=yes"]
BATCH = ["-o", "BatchMode=yes"]
# Hide console windows for children of the windowless Windows service (pythonw.exe).
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ssh_program() -> list[str]:
    """`ssh`, or the JSON argv in RCB_SSH (used by tests to substitute a fake ssh)."""
    override = os.environ.get("RCB_SSH")
    if not override:
        return ["ssh"]
    argv = json.loads(override)
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        raise BridgeError("RCB_SSH must be a JSON list of strings")
    return argv


def child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in SECRET_ENV}


def sh(script: str) -> str:
    """A remote command that runs `script` with sh, whatever the user's login shell is (fish, ...)."""
    return "sh -c " + shlex.quote(script)


def run(
    alias: str,
    command: str | None = None,
    *,
    options: Sequence[str] = (),
    batch: bool = True,
    input: bytes | None = None,
    timeout: float | None = 120,
) -> subprocess.CompletedProcess:
    """Run one command on `alias` and capture its output (bytes). Never raises on a non-zero exit."""
    argv = ssh_program() + NO_MULTIPLEX + NO_FORWARDS + (BATCH if batch else []) + list(options) + [alias]
    if command is not None:
        argv.append(command)
    try:
        return subprocess.run(
            argv,
            input=input if input is not None else b"",
            capture_output=True,
            env=child_env(),
            timeout=timeout,
            creationflags=CREATION_FLAGS,
        )
    except FileNotFoundError as error:
        raise BridgeError("ssh was not found; install OpenSSH") from error
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 255, b"", f"ssh timed out after {timeout}s".encode())


def last_line(stderr: bytes) -> str:
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def describe_failure(result: subprocess.CompletedProcess) -> str:
    return last_line(result.stderr) or f"ssh exited with status {result.returncode}"
