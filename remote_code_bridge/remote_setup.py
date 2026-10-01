"""Runs on the remote, from the archive that the installer just sent over SSH.

The installer pipes a JSON payload into a tiny bootstrap (see install.REMOTE_BOOTSTRAP), which saves
the archive to a temporary file, imports this module from it, and calls main(). Everything is
imported before the temporary file is deleted. Every change is recorded in the remote's manifest.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
from pathlib import Path

from remote_code_bridge.config import update_env_text
from remote_code_bridge.files import replace_block, resolve_symlinks, write_private
from remote_code_bridge.manifest import Manifest

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


def choose_socket(home: Path, manifest: Manifest | None = None) -> Path | None:
    """The first candidate in a private directory where a Unix socket can really be created
    (some network home directories can't hold sockets; long paths exceed the socket limit)."""
    for path in socket_candidates(home):
        if len(str(path).encode()) > 100:
            continue
        directory = path.parent
        if manifest is not None:
            manifest.add_owned_dir(directory)  # even if this one fails, it's ours to clean up
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
    try:
        return _install(payload, archive_tmp)
    finally:
        if os.path.exists(archive_tmp):
            os.unlink(archive_tmp)


def _install(payload: dict, archive_tmp: str) -> int:
    home = Path.home()
    config = home / ".config" / "remote-code-bridge"
    manifest = Manifest.load(config / "manifest.json", home)
    bin_dir = home / ".local" / "bin"
    program, code = bin_dir / "remote-code-bridge", bin_dir / "code"
    if not code_is_ours(code):
        print(f"refusing to replace {code}: it is not a remote-code-bridge link", file=sys.stderr)
        return EXIT_UNRELATED_CODE

    manifest.add_owned_dir(config)
    sock = choose_socket(home, manifest)
    if sock is None:
        manifest.save()
        print("could not create a Unix socket in ~/.cache, $XDG_RUNTIME_DIR or /tmp", file=sys.stderr)
        return EXIT_NO_SOCKET

    manifest.add_file(program)
    manifest.add_file(code)
    bin_dir.mkdir(parents=True, exist_ok=True)
    temporary = bin_dir / ".remote-code-bridge.new"
    shutil.copyfile(archive_tmp, temporary)
    os.chmod(temporary, 0o755)
    os.replace(temporary, program)
    if code.is_symlink() or code.exists():
        code.unlink()
    code.symlink_to("remote-code-bridge")

    env_file = config / "remote.env"
    old = env_file.read_text() if env_file.exists() else ""
    values = dict(payload["env"], REMOTE_CODE_BRIDGE_SOCKET=str(sock))
    write_private(env_file, update_env_text(old, values).encode())

    rc = resolve_symlinks(home / payload["rc"])
    manifest.add_block(rc, "PATH")
    old_rc = rc.read_text() if rc.exists() else ""
    mode = rc.stat().st_mode & 0o777 if rc.exists() else 0o644
    write_private(rc, replace_block(old_rc, "PATH", payload["path_line"]).encode(), mode=mode)

    manifest.save()
    print(json.dumps({"socket": str(sock)}))
    return 0
