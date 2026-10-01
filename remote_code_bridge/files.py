"""Small file helpers for the installer: private atomic writes and managed text blocks."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from remote_code_bridge import BridgeError


def resolve_symlinks(path: Path) -> Path:
    """Follow a chain of symlinks (e.g. a dotfiles-managed ~/.zshrc) to the real file."""
    for _ in range(16):
        if not path.is_symlink():
            return path
        target = Path(os.readlink(path))
        path = target if target.is_absolute() else path.parent / target
    raise BridgeError(f"too many symlinks resolving {path}")


def write_private(path: Path, data: bytes, mode: int = 0o600, follow_symlinks: bool = False) -> None:
    """Atomically replace `path` with `data`, readable only by the current user."""
    if path.is_symlink():
        if not follow_symlinks:
            raise BridgeError(f"refusing symlink destination {path}")
        path = resolve_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    if sys.platform == "win32":
        restrict_windows_acl(path)


def restrict_windows_acl(path: Path) -> None:
    """Remove inherited permissions and grant only the current user (Windows has no chmod 600)."""
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:(F)"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BridgeError(f"could not protect {path}: {result.stdout.strip() or result.stderr.strip()}")


def replace_block(text: str, name: str, body: str, at_top: bool = False) -> str:
    """Put `body` between `# >>> remote-code-bridge <name> >>>` markers, replacing any earlier copy.
    An empty body just removes the block."""
    start, end = f"# >>> remote-code-bridge {name} >>>", f"# <<< remote-code-bridge {name} <<<"
    kept, inside = [], False
    for line in text.splitlines():
        if line == start:
            inside = True
        elif line == end:
            inside = False
        elif not inside:
            kept.append(line)
    block = [start, *body.splitlines(), end] if body else []
    lines = block + kept if at_top else kept + block
    return "\n".join(lines) + "\n" if lines else ""


def path_block_for(shell: str) -> tuple[str, str]:
    """(rc file relative to $HOME, line that adds ~/.local/bin to PATH) for a login shell."""
    posix = 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'
    name = shell.rsplit("/", 1)[-1]
    if name == "fish":
        return ".config/fish/config.fish", "contains -- $HOME/.local/bin $PATH; or set -gx PATH $HOME/.local/bin $PATH"
    if name in ("zsh", "bash"):
        return f".{name}rc", posix
    return ".profile", posix
