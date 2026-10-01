"""Runs on the remote, inside the archive that the installer just sent over SSH.

The installer pipes a JSON payload into a tiny bootstrap (see install.REMOTE_BOOTSTRAP), which
saves the archive next to its final location, imports this module from it, and calls main().
Everything below must therefore be imported before the archive file is moved into place.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from remote_code_bridge.config import update_env_text
from remote_code_bridge.files import replace_block, resolve_symlinks, write_private

V1_WRAPPER_MARKER = "# Remote-side wrapper for remote-code-bridge."
EXIT_UNRELATED_CODE = 73
EXIT_NO_SOCKET = 74


def code_is_ours(code: Path) -> bool:
    if code.is_symlink():
        return os.readlink(code) == "remote-code-bridge"
    if not code.exists():
        return True
    try:
        return V1_WRAPPER_MARKER in code.read_text(errors="replace").splitlines()
    except OSError:
        return False


def socket_candidates(home: Path) -> list[Path]:
    candidates = [home / ".cache" / "remote-code-bridge" / "bridge.sock"]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates.append(Path(runtime) / "remote-code-bridge" / "bridge.sock")
    candidates.append(Path(f"/tmp/remote-code-bridge-{os.getuid()}") / "bridge.sock")
    return candidates


def choose_socket(home: Path) -> Path | None:
    """The first candidate in a private directory where a Unix socket can really be created
    (some network home directories can't hold sockets; long paths exceed the socket limit)."""
    for path in socket_candidates(home):
        if len(str(path).encode()) > 100:
            continue
        directory = path.parent
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            if directory.stat().st_uid != os.getuid() or directory.is_symlink():
                continue
            probe = directory / ".probe.sock"
            if probe.exists() or probe.is_symlink():
                probe.unlink()
            test = socket.socket(socket.AF_UNIX)
            try:
                test.bind(str(probe))
            finally:
                test.close()
            probe.unlink()
            return path
        except OSError:
            continue
    return None


def main(payload: dict, archive_tmp: str) -> int:
    home = Path.home()
    bin_dir = home / ".local" / "bin"
    code = bin_dir / "code"
    if not code_is_ours(code):
        os.unlink(archive_tmp)
        print(f"refusing to replace {code}: it is not a remote-code-bridge link", file=sys.stderr)
        return EXIT_UNRELATED_CODE
    sock = choose_socket(home)
    if sock is None:
        os.unlink(archive_tmp)
        print("could not create a Unix socket in ~/.cache, $XDG_RUNTIME_DIR or /tmp", file=sys.stderr)
        return EXIT_NO_SOCKET

    os.chmod(archive_tmp, 0o755)
    os.replace(archive_tmp, bin_dir / "remote-code-bridge")
    if code.is_symlink() or code.exists():
        code.unlink()
    code.symlink_to("remote-code-bridge")

    env_file = home / ".config" / "remote-code-bridge" / "remote.env"
    old = env_file.read_text() if env_file.exists() else ""
    values = dict(payload["env"], REMOTE_CODE_BRIDGE_SOCKET=str(sock))
    write_private(env_file, update_env_text(old, values).encode())

    rc = resolve_symlinks(home / payload["rc"])
    old_rc = rc.read_text() if rc.exists() else ""
    mode = rc.stat().st_mode & 0o777 if rc.exists() else 0o644
    write_private(rc, replace_block(old_rc, "PATH", payload["path_line"]).encode(), mode=mode)

    print(json.dumps({"socket": str(sock)}))
    return 0
