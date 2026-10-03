# Architecture

`remote-code-bridge` is one Python package (`remote_code_bridge/`, standard library only, Python 3.8+). On your machine it is installed like any Python tool (`uv tool install git+https://github.com/kshitizwagle/remote-code-bridge`, or pip), which provides the `remote-code-bridge` command. The remote gets a single-file copy of the same package (a zipapp built by `bundle.py` at install time), so it only needs `python3`.

- `remote-code-bridge serve` is the bridge on your machine, run by the login service or by hand.
- `remote-code-bridge open [code arguments]` is the remote client; invoked through a link named `code`, it selects `open` automatically.
- `install`, `update`, `uninstall`, `status`, and `generate-token` are the tools around them.

## Request flow

```text
remote: any shell, any number of sessions                 host (the machine running VS Code)
  code .                                                     remote-code-bridge serve (login service)
    │ POST /open (Bearer token)                                ├─ HTTP on 127.0.0.1:39731
    ▼                                                          │    └─ code --remote ssh-remote+devbox /path
  ~/.cache/remote-code-bridge/bridge.sock  ◄── ssh -R ─────────┴─ tunnel supervisor: ssh -N -R sock:127.0.0.1:39731 devbox
```

The remote never connects to the host directly. The host service opens the SSH connection and asks the remote sshd to listen on a Unix socket that leads back to it.

## Why the tunnel belongs to the service

Version 1 put `RemoteForward 127.0.0.1:39731 …` in `~/.ssh/config`, so every SSH session to the alias (your terminals, VS Code's Remote-SSH connection, `scp`, the installer's own probes) asked the remote to listen on the same port. Only the first succeeded, and when that session closed, `code .` broke in all the others.

A forward isn't really per-session anyway: any live forward on the remote serves every session on that remote. So version 2 keeps exactly one, owned by the host service (`tunnel.py`):

```text
PREP     ssh devbox 'mkdir -p <dir>; chmod 700 <dir>; [ -O <dir> ]; rm -f <socket>'
CONNECT  ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
             -R <socket>:127.0.0.1:<port> devbox
BACKOFF  1s, 2s, 4s … 60s (±20%), then PREP again; back to 1s after a connection lasts 60s
```

- **PREP removes a stale socket.** A tunnel that died without cleaning up (crash, network drop) leaves its socket file behind, and sshd refuses to bind over it ("remote port forwarding failed for listen path"). Removing it first also means the most recent host to connect wins.
- **Dead links are noticed** by `ServerAliveInterval`/`ServerAliveCountMax` within about 45 seconds; the supervisor then reconnects.
- **Helper SSH calls** (PREP, installer probes) use `-o ClearAllForwardings=yes -o ControlPath=none`, so nothing in your ssh config can make them request forwards or share a connection. The tunnel connection itself can't use `ClearAllForwardings`, because that option also drops forwards given on the command line.
- **States** (`connecting`, `up`, `backoff`, `auth_failed`, `disabled`) are reported by `GET /healthz` and `remote-code-bridge status`. `up` means the ssh process has stayed alive for 3 seconds with `ExitOnForwardFailure` set.
- **Orphans.** The supervisor records the ssh PID in `tunnel.pid`. If the service is killed (Windows `schtasks /End` does this), the next start stops the leftover `ssh` before connecting. On Linux the kernel also stops it via `PR_SET_PDEATHSIG`.

The remote socket path is chosen at install time by `remote_setup.choose_socket`: `~/.cache/remote-code-bridge/bridge.sock`, or `$XDG_RUNTIME_DIR/…` or `/tmp/remote-code-bridge-$UID/…` when the home directory can't hold a socket or the path is too long. The directory must be owned by the user and mode 0700.

## HTTP protocol

All requests are HTTP/1.1 with `Connection: close`, limited to 64 KiB bodies, 1 KiB lines, 16 KiB of headers, and 5 seconds in total (`httpio.py`).

| Route | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | `{"ok":true,"service":…,"version":…,"tunnel":{"state":…}}` |
| `POST /check` | bearer | `{"ok":true}`; lets `status` on the remote confirm the token |
| `POST /open` | bearer | open VS Code |

```http
POST /open
Authorization: Bearer <64 hex characters>
Content-Type: application/json

{"host": "devbox", "path": "/home/user/project", "args": ["--reuse-window"]}
```

`protocol.handle_request` checks, in order: route, body size, token (constant-time), JSON shape, absolute POSIX path without control characters, alias syntax, alias allow-list, and (for a `code.cmd` launcher) characters `cmd.exe` would interpret. The remote client reports whether the path is a folder, so the bridge builds `[code, safe flags…, (--goto|-g)?, --folder-uri|--file-uri, vscode-remote://ssh-remote+<host><path>]` and starts it as an argument list. VS Code would otherwise have to guess, and the Windows `code` launcher run from WSL guesses by looking for the path on the host, then opens a missing folder as a file. Requests from older clients without the flag, and URIs that need `%` escapes for a `code.cmd` launcher, still use `[code, safe flags…, --remote, ssh-remote+<host>, (--goto|-g)?, path]`. At most 8 launches can be in flight, and at most 4 connections are handled at once; extra ones get `503`.

## Configuration

`config.py` reads `KEY=VALUE` files; a non-empty `REMOTE_CODE_BRIDGE_*` environment variable overrides the file value.

| host.env | |
|---|---|
| `REMOTE_CODE_BRIDGE_BIND` | must be `127.0.0.1` |
| `REMOTE_CODE_BRIDGE_PORT` | default `39731`; also the tunnel's target |
| `REMOTE_CODE_BRIDGE_TOKEN` | 64 hex characters |
| `REMOTE_CODE_BRIDGE_CODE_BIN` | absolute path to VS Code's `code` |
| `REMOTE_CODE_BRIDGE_DEFAULT_HOST` | canonical alias |
| `REMOTE_CODE_BRIDGE_ALLOWED_HOSTS` | aliases for the same machine, comma-separated |
| `REMOTE_CODE_BRIDGE_DRY_RUN` | `1` replies with the command instead of running it |
| `REMOTE_CODE_BRIDGE_TUNNEL` | `0` disables the tunnel supervisor |
| `REMOTE_CODE_BRIDGE_TUNNEL_ALIAS` | alias the tunnel connects to (default: the default host) |
| `REMOTE_CODE_BRIDGE_TUNNEL_SOCKET` | absolute socket path on the remote |

| remote.env | |
|---|---|
| `REMOTE_CODE_BRIDGE_HOST_ALIAS` | sent as `host` |
| `REMOTE_CODE_BRIDGE_TOKEN` | 64 hex characters |
| `REMOTE_CODE_BRIDGE_SOCKET` | the tunnel socket; without it the client uses TCP `127.0.0.1:PORT` |

## Install, update, uninstall

`install.py` is the same code on every host OS:

1. **Find the remote.** `sshconfig.py` parses `~/.ssh/config` and its `Include`s (relative paths resolve against `~/.ssh`, as OpenSSH does). Each file must be yours and not writable by others (on Windows, compared by SID: you, SYSTEM, Administrators); files with `Match exec` are refused because `ssh -G` would run it. Candidates are probed in parallel with `BatchMode=yes`; aliases with the same `ssh -G` hostname, user, and port count as one machine.
2. **Check the remote:** Linux, Python 3.8+, and SSH login without a prompt.
3. **Install on the remote.** A short bootstrap, sent base64-encoded so no login shell can mangle it, reads a JSON payload from stdin: the single-file program, the token, and the shell rc file to use. It saves the program to a temporary file and imports `remote_setup` from it. That module chooses the socket, installs `~/.local/bin/remote-code-bridge` plus the `code` link, writes `remote.env`, adds the PATH block, records all of it in the remote manifest, and prints the socket path.
4. **Configure this host:** `host.env` (keeping unknown keys and comments), and the login service if you chose it (`--service`, `--no-service`, or a question; a reinstall keeps the earlier choice). `service.py` writes a systemd unit, a launchd plist, or a Scheduled Task that runs `python -m remote_code_bridge serve` with the package's own Python. The task has no time limit and runs on battery (Windows defaults would stop it after 72 hours or when unplugged).
5. **Clean up version 1:** the `# >>> remote-code-bridge include >>>` block in `~/.ssh/config` and `~/.ssh/remote-code-bridge/`.
6. **Verify:** wait for `tunnel: up` (with a temporary in-process bridge when there is no service) and run `remote-code-bridge status` on the remote.

**The manifest** (`manifest.py`) is what makes uninstall exact. Each machine keeps `~/.config/remote-code-bridge/manifest.json`, written *before* each change:

| Field | Meaning | On uninstall |
|---|---|---|
| `files` | files we created (rotated logs included) | deleted |
| `owned_dirs` | directories that are entirely ours (`~/.config/remote-code-bridge`, the state and socket directories) | deleted with contents |
| `created_dirs` | parents that didn't exist before we needed them (say `~/.local/bin`) | removed only if empty again |
| `blocks` | marked blocks added to files we don't own (shell rc files), and whether we created the file | block removed; file deleted only if we created it and it is now empty |
| `service`, `remote_alias` | the login service, and the remote we installed | service unregistered; remote uninstalled over SSH |

Reinstalls and updates merge into the record, never shrink it, so something created by the first install is still removed after any number of updates.

`uninstall.py` stops the service, runs `~/.local/bin/remote-code-bridge uninstall --yes` on the remote (which undoes the remote manifest, deleting the very file it runs from, which is why everything is imported up front), then removes the service and undoes the host manifest. If the remote can't be reached, the service is restarted and nothing is removed, unless `--host-only` is given. Without a manifest (an install from before manifests existed), it falls back to the locations it knows it creates.

`update.py` stops the service, upgrades the package with whatever installed it (`uv tool install --force --reinstall` when running from a uv tool environment, otherwise pip; `RCB_PACKAGE_SPEC` overrides the source), then runs the new version's `install <alias> --yes`. On Windows a helper script does this after the command exits, because a running `python.exe` can't be replaced.

## Modules

| Module | What it does |
|---|---|
| `cli.py` | argument dispatch, `status` |
| `config.py` | env files, paths |
| `protocol.py` | request validation, VS Code launch |
| `httpio.py` | deadline-bounded HTTP reading/writing |
| `server.py` | the threaded localhost server, `serve()` |
| `tunnel.py` | the tunnel supervisor |
| `client.py` | the remote `code` command |
| `ssh.py` | every `ssh` invocation (`RCB_SSH` substitutes a fake in tests) |
| `sshconfig.py` | ssh config discovery and safety checks |
| `install.py`, `remote_setup.py`, `service.py`, `update.py`, `uninstall.py`, `manifest.py`, `files.py` | installation and removal |
| `bundle.py` | builds the single-file copy sent to the remote |
| `log.py` | rotating log file with token redaction |
