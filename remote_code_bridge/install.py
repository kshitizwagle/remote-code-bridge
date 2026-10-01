"""`remote-code-bridge install [alias]`: set up (or repair) the host and the remote in one go.

Run on the machine with VS Code. Re-running it is always safe; it keeps your token and settings.

  1. find the SSH alias of a reachable Linux remote (from ~/.ssh/config, or the one you name)
  2. check the remote has python3 >= 3.8 and that this host can log in without a prompt
  3. send the archive and remote.env to the remote over SSH stdin (the token never goes on a command line)
  4. write host.env, the `remote-code-bridge` launcher and the PATH entry on this host
  5. remove v1 leftovers (the RemoteForward include in ~/.ssh/config)
  6. (re)start the host service and wait until `code .` would work end to end
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from remote_code_bridge import DEFAULT_PORT, BridgeError, bundle, generate_token, is_valid_alias, is_valid_token, ssh
from remote_code_bridge.client import call
from remote_code_bridge.config import RemoteConfig, home_dir, read_values, update_env_text
from remote_code_bridge.files import path_block_for, replace_block, resolve_symlinks, write_private
from remote_code_bridge.service import ServiceManager
from remote_code_bridge.sshconfig import discover_aliases, identity

MIN_REMOTE_PYTHON = (3, 8)
PROBE = ssh.sh(
    'uname -s; uname -m; python3 -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2>/dev/null '
    "|| echo none"
)
# Runs on the remote: save the archive from the JSON on stdin, then let it finish the job
# (remote_setup.main). Sent base64-encoded so no shell (bash, zsh, fish) can mangle it.
REMOTE_BOOTSTRAP = """
import base64, json, os, sys, tempfile
payload = json.load(sys.stdin)
bin_dir = os.path.expanduser("~/.local/bin")
os.makedirs(bin_dir, exist_ok=True)
fd, archive = tempfile.mkstemp(dir=bin_dir, prefix=".remote-code-bridge.")
with os.fdopen(fd, "wb") as stream:
    stream.write(base64.b64decode(payload["archive"]))
sys.path.insert(0, archive)
from remote_code_bridge.remote_setup import main
sys.exit(main(payload, archive))
"""
NO_PROMPT_HELP = (
    "The host service logs in to {alias} by itself, so SSH must work without a password prompt.\n"
    "  - use a key without a passphrase, or load it into ssh-agent (`ssh-add`)\n"
    "  - Windows: start the 'OpenSSH Authentication Agent' service, then `ssh-add`\n"
    "  - check with: ssh -o BatchMode=yes {alias} true"
)
SSHD_HELP = (
    "If the tunnel never comes up, check that the remote sshd allows forwarding to Unix sockets\n"
    "(AllowStreamLocalForwarding yes, which is the default, and no DisableForwarding)."
)


def step(message: str) -> None:
    print(f"==> {message}", file=sys.stderr)


def install(
    alias: str | None = None,
    assume_yes: bool = False,
    *,
    services: ServiceManager | None = None,
    verify: bool = True,
) -> None:
    home = home_dir()
    services = services or ServiceManager()
    ssh_config = Path(os.environ.get("RCB_SSH_CONFIG") or home / ".ssh" / "config")
    known_aliases = discover_aliases(ssh_config, home)

    if alias:
        if not is_valid_alias(alias):
            raise BridgeError("SSH alias contains unsupported characters")
        step(f"Checking SSH alias {alias}")
        target = check_explicit_alias(alias)
    else:
        step("Looking for a reachable Linux SSH alias")
        target = discover_target(known_aliases, ssh_config, interactive=sys.stdin.isatty() and not assume_yes)
    allowed = equivalent_aliases(target, known_aliases)

    host_env = home / ".config" / "remote-code-bridge" / "host.env"
    existing = read_values(host_env)
    token = existing.get("REMOTE_CODE_BRIDGE_TOKEN", "")
    if not is_valid_token(token):
        token = generate_token()
    port = existing.get("REMOTE_CODE_BRIDGE_PORT", "")
    port = port if port.isdigit() and 1 <= int(port) <= 65535 else str(DEFAULT_PORT)
    if existing.get("REMOTE_CODE_BRIDGE_BIND", "127.0.0.1") != "127.0.0.1":
        raise BridgeError("REMOTE_CODE_BRIDGE_BIND must be 127.0.0.1")
    code_bin = find_code(existing.get("REMOTE_CODE_BRIDGE_CODE_BIN"))
    archive = bundle.archive_bytes()

    step(f"Installing on {target} over SSH")
    remote_socket = install_remote(target, archive, token)

    step("Installing on this host")
    services.stop()
    host_archive = home / ".local" / "share" / "remote-code-bridge" / "remote-code-bridge.pyz"
    write_private(host_archive, archive)
    write_launcher(home, sys.executable, host_archive)
    values = {
        "REMOTE_CODE_BRIDGE_BIND": "127.0.0.1",
        "REMOTE_CODE_BRIDGE_PORT": port,
        "REMOTE_CODE_BRIDGE_TOKEN": token,
        "REMOTE_CODE_BRIDGE_CODE_BIN": code_bin,
        "REMOTE_CODE_BRIDGE_DEFAULT_HOST": target,
        "REMOTE_CODE_BRIDGE_ALLOWED_HOSTS": ",".join(allowed),
        "REMOTE_CODE_BRIDGE_DRY_RUN": existing.get("REMOTE_CODE_BRIDGE_DRY_RUN", "0"),
        "REMOTE_CODE_BRIDGE_TUNNEL": existing.get("REMOTE_CODE_BRIDGE_TUNNEL", "1"),
        "REMOTE_CODE_BRIDGE_TUNNEL_ALIAS": target,
        "REMOTE_CODE_BRIDGE_TUNNEL_SOCKET": remote_socket,
    }
    old_text = host_env.read_text(encoding="utf-8") if host_env.exists() else ""
    write_private(host_env, update_env_text(old_text, values).encode())
    remove_v1_leftovers(home, ssh_config)

    step("Starting the host service")
    services.install(sys.executable, host_archive)
    if verify:
        step("Checking the tunnel")
        verify_install(target, int(port))
    print(f"remote-code-bridge: installed for {', '.join(allowed)}. Run `code .` in any SSH session on {target}.")


# -- choosing the remote ------------------------------------------------------------------------


def probe(alias: str, batch: bool) -> tuple[str, str, tuple[int, int] | None] | str:
    """(os, arch, python version) of the remote, or an error message."""
    result = ssh.run(alias, PROBE, batch=batch, options=["-o", "ConnectTimeout=5"] if batch else [], timeout=60)
    lines = result.stdout.decode("utf-8", "replace").split()
    if result.returncode != 0 or len(lines) < 3:
        return ssh.describe_failure(result)
    version = (int(lines[2]), int(lines[3])) if lines[2] != "none" and len(lines) >= 4 else None
    return lines[0], lines[1], version


def require_linux_with_python(alias: str, found: tuple[str, str, tuple[int, int] | None]) -> None:
    system, _arch, version = found
    if system != "Linux":
        raise BridgeError(f"{alias} runs {system}; the remote must be Linux")
    if version is None or version < MIN_REMOTE_PYTHON:
        have = "no python3" if version is None else f"python {version[0]}.{version[1]}"
        raise BridgeError(f"{alias} needs python3 3.8 or newer (found {have}); e.g. `sudo apt install python3`")


def check_explicit_alias(alias: str) -> str:
    found = probe(alias, batch=False)  # may prompt, e.g. to accept a new host key
    if isinstance(found, str):
        raise BridgeError(f"cannot reach SSH alias {alias}: {found}")
    require_linux_with_python(alias, found)
    if ssh.run(alias, "true", timeout=60).returncode != 0:
        raise BridgeError(NO_PROMPT_HELP.format(alias=alias))
    return alias


def discover_target(aliases: list[str], ssh_config: Path, interactive: bool) -> str:
    if not aliases:
        raise BridgeError(f"no concrete SSH Host aliases found in {ssh_config}; pass one explicitly")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda alias: probe(alias, batch=True), aliases))
    linux = [
        (alias, found) for alias, found in zip(aliases, results) if not isinstance(found, str) and found[0] == "Linux"
    ]
    if not linux:
        raise BridgeError(
            "no configured SSH alias was reachable as Linux without a password prompt; "
            "pass an alias explicitly after fixing SSH access"
        )
    # Several aliases often point at the same machine; only ask when they are really different.
    machines: dict[object, str] = {}
    for alias, _found in linux:
        machines.setdefault(identity(alias) or alias, alias)
    choices = list(machines.values())
    if len(choices) > 1:
        if not interactive:
            raise BridgeError(f"several Linux remotes are reachable ({', '.join(choices)}); pass the one you want")
        for number, alias in enumerate(choices, 1):
            print(f"  {number}) {alias}", file=sys.stderr)
        answer = input("Install for which remote? [1] ").strip() or "1"
        if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
            raise BridgeError("no remote chosen")
        choices = [choices[int(answer) - 1]]
    chosen = choices[0]
    require_linux_with_python(chosen, dict(linux)[chosen])  # type: ignore[arg-type]
    return chosen


def equivalent_aliases(target: str, aliases: list[str]) -> list[str]:
    """Every alias that reaches the same hostname/user/port, so `code .` works whichever you used."""
    wanted = identity(target)
    same = [alias for alias in aliases if alias != target and wanted is not None and identity(alias) == wanted]
    return [target, *same]


# -- host side ----------------------------------------------------------------------------------


def find_code(configured: str | None) -> str:
    if configured:
        found = configured if os.path.isabs(configured) and os.path.isfile(configured) else shutil.which(configured)
        if found:
            return found
    names = ("code.cmd", "code.exe", "code") if sys.platform == "win32" else ("code",)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise BridgeError(
        "could not find VS Code's `code` command in PATH; in VS Code run "
        "'Shell Command: Install code command in PATH', then retry"
    )


def write_launcher(home: Path, python: str, archive: Path) -> None:
    bin_dir = home / ".local" / "bin"
    if sys.platform == "win32":
        write_private(bin_dir / "remote-code-bridge.cmd", f'@"{python}" "{archive}" %*\r\n'.encode())
        add_to_windows_path(bin_dir)
        return
    import shlex

    script = f'#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(archive))} "$@"\n'
    write_private(bin_dir / "remote-code-bridge", script.encode(), mode=0o755)
    rc_name, line = path_block_for(os.environ.get("SHELL", ""))
    rc = resolve_symlinks(home / rc_name)
    old = rc.read_text(encoding="utf-8") if rc.exists() else ""
    mode = rc.stat().st_mode & 0o777 if rc.exists() else 0o644
    write_private(rc, replace_block(old, "PATH", line).encode(), mode=mode)


def add_to_windows_path(directory: Path) -> None:
    import ctypes
    import winreg  # type: ignore[import-not-found]

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in current.split(";") if entry]
        if any(os.path.normcase(entry) == os.path.normcase(str(directory)) for entry in entries):
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([str(directory), *entries]))
    # Tell Explorer (and so new terminals) that the environment changed.
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)


def remove_v1_leftovers(home: Path, ssh_config: Path) -> None:
    """v1 put a RemoteForward on every SSH session via an Include in ~/.ssh/config. Remove it."""
    if ssh_config.exists():
        text = ssh_config.read_text(encoding="utf-8")
        if "# >>> remote-code-bridge include >>>" in text:
            write_private(ssh_config, replace_block(text, "include", "").encode(), follow_symlinks=True)
    managed = home / ".ssh" / "remote-code-bridge"
    if managed.is_symlink():
        managed.unlink()
    elif managed.is_dir():
        shutil.rmtree(managed)
    old_exe = home / ".local" / "bin" / "remote-code-bridge.exe"
    if old_exe.exists():
        try:
            old_exe.unlink()
        except OSError as error:
            print(f"remote-code-bridge: could not remove {old_exe}: {error}", file=sys.stderr)


# -- remote side --------------------------------------------------------------------------------


def install_remote(alias: str, archive: bytes, token: str) -> str:
    """Install on the remote and return the Unix socket path it chose for the tunnel."""
    shell = ssh.run(alias, 'printf %s "$SHELL"').stdout.decode("utf-8", "replace").strip()
    rc_name, line = path_block_for(shell)
    payload = {
        "archive": base64.b64encode(archive).decode("ascii"),
        "env": {"REMOTE_CODE_BRIDGE_HOST_ALIAS": alias, "REMOTE_CODE_BRIDGE_TOKEN": token},
        "rc": rc_name,
        "path_line": line,
    }
    encoded = base64.b64encode(REMOTE_BOOTSTRAP.encode()).decode("ascii")
    command = f"python3 -c \"import base64; exec(base64.b64decode('{encoded}'))\""
    result = ssh.run(alias, command, input=json.dumps(payload).encode(), timeout=300)
    if result.returncode != 0:
        raise BridgeError(f"remote install on {alias} failed: {ssh.describe_failure(result)}")
    try:
        return json.loads(result.stdout.decode().strip().splitlines()[-1])["socket"]
    except (ValueError, KeyError, IndexError) as error:
        raise BridgeError(f"remote install on {alias} returned unexpected output") from error


def verify_install(alias: str, port: int, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    tunnel: dict = {}
    while time.monotonic() < deadline:
        try:
            tunnel = call(RemoteConfig(port=port), "GET", "/healthz").get("tunnel") or {}
        except BridgeError:
            tunnel = {"state": "not running", "last_error": "the host service is not answering yet"}
        if tunnel.get("state") == "up":
            break
        time.sleep(1)
    else:
        problem = tunnel.get("last_error") or tunnel.get("state")
        help_text = NO_PROMPT_HELP.format(alias=alias) if tunnel.get("state") == "auth_failed" else SSHD_HELP
        raise BridgeError(f"installed, but the tunnel to {alias} did not come up: {problem}\n{help_text}")
    result = ssh.run(alias, "$HOME/.local/bin/remote-code-bridge status", timeout=60)
    if result.returncode != 0:
        output = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
        raise BridgeError(f"installed, but the remote check failed:\n{output}")
