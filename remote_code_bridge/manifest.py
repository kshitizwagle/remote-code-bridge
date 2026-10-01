"""A record of everything installation changed on one machine, so uninstall can undo exactly that.

Each machine (host and remote) keeps its own `~/.config/remote-code-bridge/manifest.json`:

  files         files we created (deleted on uninstall, with rotated `.1`..`.3` copies of logs)
  owned_dirs    directories that belong to us entirely (deleted with their contents)
  created_dirs  parent directories that did not exist before we needed them, e.g. ~/.local/bin
                (removed on uninstall only if they are empty again)
  blocks        `# >>> remote-code-bridge NAME >>>` blocks we added to files we don't own,
                such as ~/.bashrc; `created_file` says whether we created that file too
  service       the login service we registered ("linux", "darwin", "win32"), if any
  remote_alias  host only: the SSH alias whose remote side we installed

Recording happens *before* each change, so a directory is only claimed when it was really
missing. Re-installing and updating merge into the existing record, never shrink it.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from remote_code_bridge.files import replace_block, write_private

ROTATED_LOGS = 3


class Manifest:
    def __init__(self, path: Path, home: Path):
        self.path = path
        self.home = home
        self.files: list[str] = []
        self.owned_dirs: list[str] = []
        self.created_dirs: list[str] = []
        self.blocks: list[dict[str, Any]] = []
        self.service: str | None = None
        self.remote_alias: str | None = None

    # -- loading and saving -------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path, home: Path) -> Manifest:
        manifest = cls(path, home)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        manifest.files = list(data.get("files", []))
        manifest.owned_dirs = list(data.get("owned_dirs", []))
        manifest.created_dirs = list(data.get("created_dirs", []))
        manifest.blocks = list(data.get("blocks", []))
        manifest.service = data.get("service")
        manifest.remote_alias = data.get("remote_alias")
        return manifest

    def exists(self) -> bool:
        return self.path.exists()

    def save(self) -> None:
        self.add_file(self.path)
        data = {
            "version": 1,
            "files": self.files,
            "owned_dirs": self.owned_dirs,
            "created_dirs": self.created_dirs,
            "blocks": self.blocks,
            "service": self.service,
            "remote_alias": self.remote_alias,
        }
        write_private(self.path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))

    # -- recording (call before creating the thing) ------------------------------------------

    def _note_missing_parents(self, path: Path) -> None:
        """Claim every missing directory between `path` and the home directory."""
        missing = []
        for parent in path.parents:
            if parent == self.home or self.home not in parent.parents:
                break  # never claim the home directory itself, or anything outside it
            if parent.exists():
                break
            missing.append(str(parent))
        for directory in missing:
            if directory not in self.created_dirs and directory not in self.owned_dirs:
                self.created_dirs.append(directory)

    def add_file(self, path: Path) -> None:
        if str(path) not in self.files:
            if not path.exists():
                self._note_missing_parents(path)
            self.files.append(str(path))

    def add_owned_dir(self, path: Path) -> None:
        if str(path) not in self.owned_dirs:
            if not path.exists():
                self._note_missing_parents(path)
            self.owned_dirs.append(str(path))
            if str(path) in self.created_dirs:
                self.created_dirs.remove(str(path))

    def add_block(self, path: Path, name: str) -> None:
        for block in self.blocks:
            if block["path"] == str(path) and block["name"] == name:
                return
        created = not path.exists()
        if created:
            self._note_missing_parents(path)
        self.blocks.append({"path": str(path), "name": name, "created_file": created})

    # -- undoing ------------------------------------------------------------------------------

    def describe(self) -> list[str]:
        lines = [f"remove {path}" for path in self.files + self.owned_dirs if os.path.lexists(path)]
        lines += [
            f"remove the remote-code-bridge lines from {block['path']}"
            for block in self.blocks
            if _has_block(Path(block["path"]), block["name"])
        ]
        if self.service:
            lines.append("remove the login service")
        return lines

    def undo(self) -> list[str]:
        """Remove everything recorded. Returns problems (as messages) instead of stopping at the first."""
        problems: list[str] = []
        for block in self.blocks:
            path = Path(block["path"])
            try:
                if path.exists():
                    text = path.read_text(encoding="utf-8")
                    cleaned = replace_block(text, block["name"], "")
                    if block.get("created_file") and not cleaned.strip():
                        path.unlink()
                    elif cleaned != text:
                        mode = path.stat().st_mode & 0o777
                        write_private(path, cleaned.encode("utf-8"), mode=mode)
            except OSError as error:
                problems.append(f"could not clean {path}: {error}")
        for name in self.files:
            rotated = [f"{name}.{index}" for index in range(1, ROTATED_LOGS + 1)] if name.endswith(".log") else []
            for path in [name, *rotated]:
                try:
                    if os.path.lexists(path):
                        os.unlink(path)
                except OSError as error:
                    problems.append(f"could not remove {path}: {error}")
        for name in self.owned_dirs:
            if os.path.islink(name):
                os.unlink(name)
            elif os.path.isdir(name):
                shutil.rmtree(name, onerror=lambda _f, path, error: problems.append(f"could not remove {path}"))
        # Deepest first, so ~/.local/bin goes before ~/.local. Non-empty means someone else uses it.
        for name in sorted(self.created_dirs, key=lambda path: path.count(os.sep), reverse=True):
            try:
                os.rmdir(name)
            except OSError:
                pass
        return problems


def _has_block(path: Path, name: str) -> bool:
    try:
        return f"# >>> remote-code-bridge {name} >>>" in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
