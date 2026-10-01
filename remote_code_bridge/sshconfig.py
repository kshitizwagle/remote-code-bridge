"""Reading ~/.ssh/config to find SSH aliases, and asking `ssh -G` which ones are the same machine.

This module only *parses* config files. `ssh -G` does not run ProxyCommand and friends, but it does
run `Match exec`, so files containing that are refused. Files must also be yours and not writable
by anyone else, because their contents decide which commands your ssh runs.
"""

from __future__ import annotations

import glob
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from remote_code_bridge import BridgeError, is_valid_alias, ssh

MAX_DEPTH, MAX_FILES, MAX_ALIASES = 16, 128, 256
# Owners/writers that are as trusted as you on Windows: SYSTEM and the Administrators group.
WINDOWS_TRUSTED_SIDS = ("S-1-5-18", "S-1-5-32-544")


def split_words(text: str, where: str) -> list[str]:
    """Split ssh_config arguments: whitespace-separated, double quotes group, `#` starts a comment."""
    words, current, quoted, has_word = [], [], False, False
    for char in text:
        if char == '"':
            quoted, has_word = not quoted, True
        elif char.isspace() and not quoted:
            if has_word:
                words.append("".join(current))
            current, has_word = [], False
        elif char == "#" and not quoted and not has_word:
            break
        else:
            current.append(char)
            has_word = True
    if quoted:
        raise BridgeError(f"unbalanced quotes in {where}")
    if has_word:
        words.append("".join(current))
    return words


def parse_line(line: str, where: str) -> tuple[str, list[str]] | None:
    """`Keyword args` or `Keyword=args` -> (lower-case keyword, args). None for blank/comment lines."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    keyword, rest = line, ""
    for index, char in enumerate(line):
        if char.isspace() or char == "=":
            keyword, rest = line[:index], line[index:].strip()
            rest = rest[1:].strip() if rest.startswith("=") else rest
            break
    return keyword.lower(), split_words(rest, where)


class Discovery:
    def __init__(self, home: Path):
        self.home = home
        self.aliases: list[str] = []
        self.seen: set = set()

    def read(self, path: Path, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise BridgeError(f"SSH Include recursion exceeds {MAX_DEPTH} levels")
        if not path.is_file():
            return
        real = path.resolve()
        if real in self.seen:
            return
        if len(self.seen) >= MAX_FILES:
            raise BridgeError("SSH Include file limit exceeded")
        self.seen.add(real)
        check_file_is_safe(real)
        for line in real.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = parse_line(line, str(real))
            if parsed is None:
                continue
            keyword, words = parsed
            if keyword == "match" and any(word.lower() == "exec" for word in words):
                raise BridgeError(f"refusing `Match exec` in {real}: ssh -G would run it")
            if keyword == "host":
                for word in words:
                    self._add_alias(word)
            elif keyword == "include":
                if not words:
                    raise BridgeError(f"empty SSH Include in {real}")
                for pattern in words:
                    for include in sorted(glob.glob(str(self._include_path(pattern)))):
                        self.read(Path(include), depth + 1)

    def _include_path(self, pattern: str) -> Path:
        # OpenSSH resolves relative Include paths against ~/.ssh, not the including file's directory.
        if pattern.startswith("~/"):
            return self.home / pattern[2:]
        if os.path.isabs(pattern):
            return Path(pattern)
        return self.home / ".ssh" / pattern

    def _add_alias(self, word: str) -> None:
        if word.startswith("!") or any(char in word for char in "*?[") or not is_valid_alias(word):
            return
        if word not in self.aliases:
            if len(self.aliases) >= MAX_ALIASES:
                raise BridgeError("SSH alias limit exceeded")
            self.aliases.append(word)


def discover_aliases(config: Path, home: Path) -> list[str]:
    """Concrete Host aliases from `config` and everything it includes, in file order."""
    discovery = Discovery(home)
    discovery.read(config)
    return discovery.aliases


def check_file_is_safe(path: Path) -> None:
    if sys.platform == "win32":
        _check_windows_acl(path)
        return
    info = path.stat()
    if info.st_uid != os.getuid():
        raise BridgeError(f"refusing SSH config {path}: it is not owned by you")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise BridgeError(f"refusing SSH config {path}: it is writable by group/others")


_ACL_SCRIPT = r"""
$acl = Get-Acl -LiteralPath $env:RCB_ACL_PATH
$sid = [System.Security.Principal.SecurityIdentifier]
$writers = @($acl.GetAccessRules($true, $true, $sid) | Where-Object {
    $_.AccessControlType -eq 'Allow' -and
    ($_.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -eq 0 -and
    ($_.FileSystemRights.ToString() -match 'Write|Modify|FullControl|ChangePermissions|TakeOwnership')
} | ForEach-Object { $_.IdentityReference.Value })
@{ owner = $acl.GetOwner($sid).Value; me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
   writers = $writers } | ConvertTo-Json -Compress
"""


def _check_windows_acl(path: Path) -> None:
    """Compare SIDs, not names: names like `BUILTIN\\Administrators` are translated on some systems."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _ACL_SCRIPT],
        capture_output=True,
        text=True,
        env=dict(os.environ, RCB_ACL_PATH=str(path)),
        creationflags=ssh.CREATION_FLAGS,
    )
    if result.returncode != 0:
        raise BridgeError(f"could not read permissions of {path}: {result.stderr.strip()}")
    acl = json.loads(result.stdout)
    trusted = (acl["me"], *WINDOWS_TRUSTED_SIDS)
    if acl["owner"] not in trusted:
        raise BridgeError(f"refusing SSH config {path}: it is not owned by you")
    writers = acl["writers"] if isinstance(acl["writers"], list) else [acl["writers"]]
    if any(writer not in trusted for writer in writers if writer):
        raise BridgeError(f"refusing SSH config {path}: it is writable by other users")


def identity(alias: str) -> tuple[str, str, str] | None:
    """(hostname, user, port) that ssh would really connect to, or None if ssh can't resolve it."""
    result = ssh.run_local(["-G", alias])
    if result.returncode != 0:
        return None
    values = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition(" ")
        values.setdefault(key.lower(), value.strip())
    found = tuple(values.get(key, "") for key in ("hostname", "user", "port"))
    return found if all(found) else None  # type: ignore[return-value]
