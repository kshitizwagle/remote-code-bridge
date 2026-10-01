[![release](https://github.com/kshitizwagle/remote-code-bridge/actions/workflows/release.yml/badge.svg)](https://github.com/kshitizwagle/remote-code-bridge/actions/workflows/release.yml)

---

# remote-code-bridge

Run `code .` on a Linux machine you reached through SSH, and that directory opens in VS Code on your macOS, Linux, or Windows machine.

```text
remote: code .  →  SSH tunnel  →  bridge on your machine  →  code --remote ssh-remote+devbox /remote/path
```

## Features

- **Works from Windows, macOS, and Linux.** Your machine (where VS Code runs) can be any of the three. The remote is any Linux box you reach over SSH.
- **Every SSH session works, however many you open.** Open ten terminals to the same remote, close them in any order, and let VS Code's own Remote-SSH connection come and go; `code .` works in all of them. There are no "remote port forwarding failed for listen port" errors.
- **Heals itself.** One small service on your machine owns the tunnel, not any single terminal. After sleep, Wi-Fi changes, a dropped connection, or a remote reboot, it reconnects on its own (dead links are noticed within about 45 seconds), and clears any leftover from the old connection first.
- **Handles bursts.** Many `code` commands at the same moment queue up for a moment instead of failing.
- **Feels like the real `code`.** `code .`, `code some/dir`, `code -r` / `--reuse-window`, `code -n` / `--new-window`, and `code -g file:line` / `--goto` work as you'd expect. Relative paths and `..` are resolved on the remote.
- **One command to install, one to update.** The installer finds a reachable Linux remote in your `~/.ssh/config` (following `Include` files), or uses the alias you name. If several different remotes are reachable it asks which one you mean. It sets up both machines and the login service, then checks end to end that `code .` will work. `remote-code-bridge update` installs the latest release, and `remote-code-bridge status` tells you what's wrong if something is.
- **Knows your aliases.** Aliases that reach the same machine (for example `devbox` and `devbox.lan`) all work, whichever one you connected with. Configs that use `ProxyCommand`/`ProxyJump` jump hosts are fine.
- **Leaves your SSH config alone.** Nothing is added to `~/.ssh/config`, and installs from version 1 are cleaned up automatically.
- **Private by design.** The service listens only on your machine's `127.0.0.1`. On the remote, the tunnel is a socket in a directory only your account can open. Each request carries a random 64-character token that never appears on a command line. VS Code is started without a shell, and only safe flags are passed. See [Security](docs/SECURITY.md).
- **Runs as a proper background service:** a systemd user service on Linux, a launchd agent on macOS, a Scheduled Task on Windows (it keeps running on battery, with no time limit). It starts when you log in.
- **Tiny and dependency-free.** Plain Python 3.8+ with only the standard library, shipped as one file (`remote-code-bridge.pyz`) that is checksum-verified on install and update.

## Requirements

- **Your machine (the host):** Python 3.8+, OpenSSH, VS Code with its `code` command in `PATH`, and the Remote - SSH extension.
- **The remote:** Linux with Python 3.8+ (`python3`).
- **SSH login without a prompt** from your machine to the remote: a key without a passphrase, or your key loaded into ssh-agent (`ssh-add`; on Windows start the *OpenSSH Authentication Agent* service first). The bridge service connects on its own, so it can't type a password.

## Install

On the machine that runs VS Code:

```sh
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s -- devbox
```

On Windows PowerShell:

```powershell
$env:RCB_SSH_ALIAS = 'devbox'
irm https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.ps1 | iex
```

Replace `devbox` with your SSH alias from `~/.ssh/config`, or leave it out to let the installer find a reachable Linux alias. If several different remotes are reachable it asks which one you mean (or, when it can't ask, tells you to pass one). Aliases that point at the same machine (same hostname, user, and port) are treated as one remote, and `code .` works whichever of them you used to connect.

Then, in any SSH session on the remote:

```sh
cd ~/project
code .
```

For a reproducible install, use a versioned release URL and check it against the matching `install.sh.sha256` or `install.ps1.sha256`.

### What installation sets up

- **Remote:** `~/.local/bin/remote-code-bridge` (the archive), a `~/.local/bin/code` link to it, `~/.config/remote-code-bridge/remote.env`, and a `~/.local/bin` PATH entry in your shell startup file. It refuses to replace a `code` command that isn't its own.
- **Host:** `~/.local/share/remote-code-bridge/remote-code-bridge.pyz`, a `remote-code-bridge` launcher in `~/.local/bin` (on your PATH), `~/.config/remote-code-bridge/host.env`, and a login service: a systemd user service on Linux, a launchd agent on macOS, a Scheduled Task on Windows.
- A random 64-character token shared by both sides. It travels to the remote over SSH standard input, never on a command line.
- **Nothing in your `~/.ssh/config`.** (Version 1 added a `RemoteForward` there; the installer removes it.)

Re-running the installer is always safe: it keeps your token and settings.

## Check and update

```sh
remote-code-bridge status     # on either machine: is the service running, is the tunnel up?
remote-code-bridge update     # on the host: install the latest release for the saved alias
```

`update` downloads the latest `remote-code-bridge.pyz`, checks its SHA-256, and re-runs its installer. If GitHub rate-limits you (HTTP 403/429), export a token in the current shell and retry; it is used only for that retry:

```sh
export GH_TOKEN=github_pat_...
```

**Upgrading from version 1:** run `remote-code-bridge update` as usual. It installs version 2, keeps your token, and removes the old `RemoteForward` include from `~/.ssh/config`.

## Troubleshooting

| You see | Do this |
|---|---|
| `no tunnel from your host` on the remote | Run `remote-code-bridge status` on the host. The host must be awake and logged in. |
| `tunnel: auth_failed` | The service can't log in without a prompt: `ssh-add` your key (Windows: start the OpenSSH Authentication Agent service), then check `ssh -o BatchMode=yes devbox true`. |
| `tunnel: backoff` with a forwarding error | The remote sshd must allow forwarding to Unix sockets (`AllowStreamLocalForwarding yes`, the default; no `DisableForwarding`). |
| `token: rejected` | Re-run the installer from the host. |

Host logs: `~/.local/state/remote-code-bridge/bridge.log` (Linux), `~/Library/Logs/remote-code-bridge.log` (macOS), `%LOCALAPPDATA%\remote-code-bridge\bridge.log` (Windows).

## How it works

1. The host service listens on `127.0.0.1:39731` and keeps one SSH connection to the remote: `ssh -N -R ~/.cache/remote-code-bridge/bridge.sock:127.0.0.1:39731 devbox`. That makes a Unix socket on the remote, in a directory only you can read, lead back to the service.
2. `code .` on the remote resolves the path and sends an authenticated `POST /open` through that socket.
3. The service checks the token, alias, path, and flags, then starts `code --remote ssh-remote+devbox /path` without a shell.

See [Architecture](docs/ARCHITECTURE.md) and [Security](docs/SECURITY.md) for details.

## Development

```sh
python3 -m pip install pytest pytest-cov ruff
python3 -m pytest                       # unit and integration tests (fake ssh, no network)
ruff check . && ruff format --check .
./scripts/smoke-test.sh                 # the built archive, serve + code
./scripts/native-linux-install-update-test.sh   # Docker: real sshd, install, 12 sessions, recovery, update
./scripts/build.sh 2.0.0                # dist/remote-code-bridge.pyz + .sha256
```

Run the pieces by hand without installing (`REMOTE_CODE_BRIDGE_TUNNEL=0` skips the SSH tunnel; the client then uses TCP `127.0.0.1:PORT`):

```sh
export REMOTE_CODE_BRIDGE_TOKEN="$(python3 -m remote_code_bridge generate-token)"
REMOTE_CODE_BRIDGE_DRY_RUN=1 REMOTE_CODE_BRIDGE_TUNNEL=0 REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox \
  python3 -m remote_code_bridge serve
# another terminal, same token:
REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox python3 -m remote_code_bridge open .
```

Configuration lives in `~/.config/remote-code-bridge/host.env` and `remote.env`; non-empty `REMOTE_CODE_BRIDGE_*` environment variables override them. Do not set a different `REMOTE_CODE_BRIDGE_TOKEN` in your shell for an installed setup, because it would override the saved one.

Releases: publishing a release, pushing a `v*`/numeric tag, or **Actions → release → Run workflow** runs the tests, builds `remote-code-bridge.pyz`, pins `install.sh`/`install.ps1` to that release, and uploads all of them with SHA-256 files.

## License

MIT
