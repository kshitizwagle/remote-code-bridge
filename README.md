[![smoke](https://github.com/kshitizwagle/remote-code-bridge/actions/workflows/smoke.yml/badge.svg)](https://github.com/kshitizwagle/remote-code-bridge/actions/workflows/smoke.yml)

---

# remote-code-bridge

Run `code .` on a Linux machine you reached through SSH, and that directory opens in VS Code on your Windows, macOS, or Linux machine.

```text
remote: code .  →  SSH tunnel  →  bridge on your machine  →  code --remote ssh-remote+devbox /remote/path
```

```sh
uv tool install remote-code-bridge
remote-code-bridge install devbox
```

## Features

- **Works from Windows, macOS, and Linux.** Your machine (where VS Code runs) can be any of the three; the remote is any Linux box you reach over SSH.
- **Every SSH session works, however many you open.** Open ten terminals to the same remote, close them in any order, let VS Code's own Remote-SSH connection come and go: `code .` works in all of them. No more "remote port forwarding failed for listen port" errors.
- **Heals itself.** One small bridge on your machine owns the tunnel, not any single terminal. After sleep, Wi-Fi changes, a dropped connection, or a remote reboot it reconnects on its own (dead links are noticed within about 45 seconds) and clears any leftover from the old connection first.
- **Handles bursts.** Many `code` commands at the same moment queue up for an instant instead of failing.
- **Feels like the real `code`.** `code .`, `code some/dir`, `code -r` / `--reuse-window`, `code -n` / `--new-window`, `code -g file:line` / `--goto`. Relative paths and `..` are resolved on the remote.
- **Installs like any Python tool.** `uv tool install remote-code-bridge` (or `pip install`) from PyPI. One command then sets up the remote, and the remote only needs `python3`.
- **Your choice of how it runs.** Let it start at login as a background service (a systemd user service on Linux, a launchd agent on macOS, a Scheduled Task on Windows that keeps running on battery with no time limit), or run `remote-code-bridge serve` yourself when you want it.
- **Uninstalls without a trace.** Each machine keeps a record of exactly what was installed: files, folders it had to create, lines added to shell startup files, the service. `remote-code-bridge uninstall` removes exactly that, on both machines, and leaves everything that was there before.
- **Knows your aliases.** Finds a reachable Linux remote in your `~/.ssh/config` (following `Include` files), asks which one when several different remotes are reachable, and treats aliases that reach the same machine (say `devbox` and `devbox.lan`) as one. `ProxyCommand`/`ProxyJump` jump hosts are fine.
- **Leaves your SSH config alone.** Nothing is added to `~/.ssh/config`. Installations from version 1 are cleaned up automatically.
- **Private by design.** The bridge listens only on your machine's `127.0.0.1`. On the remote, the tunnel is a socket in a directory only your account can open. Every request carries a random 64-character token that never appears on a command line. VS Code is started without a shell, and only safe flags are passed through. See [Security](https://github.com/kshitizwagle/remote-code-bridge/blob/master/docs/SECURITY.md).
- **Tiny and dependency-free.** Plain Python 3.8+, standard library only.

## Requirements

- **Your machine:** Python 3.8+, [uv](https://docs.astral.sh/uv/) (or pip), OpenSSH, VS Code with its `code` command in `PATH`, and the Remote - SSH extension.
- **The remote:** Linux with `python3` 3.8+.
- **SSH login without a prompt** from your machine to the remote: a key without a passphrase, or your key loaded into ssh-agent (`ssh-add`; on Windows start the *OpenSSH Authentication Agent* service first). The bridge connects by itself, so it can't type a password.

## Install

```sh
uv tool install remote-code-bridge
remote-code-bridge install devbox
```

Replace `devbox` with your SSH alias from `~/.ssh/config`, or leave it out to let the installer find a reachable Linux alias. It asks whether to start the bridge automatically when you log in; answer up front with `--service` or `--no-service`. It then checks end to end that `code .` will work.

Then, in any SSH session on the remote:

```sh
cd ~/project
code .
```

If you chose not to install the service, keep `remote-code-bridge serve` running on your machine while you work.

<details>
<summary>Without uv</summary>

```sh
python3 -m pip install --user remote-code-bridge
remote-code-bridge install devbox
```

`pipx install remote-code-bridge` works too. To pin a version, install `remote-code-bridge==2.1.0`. To run unreleased code, install `git+https://github.com/kshitizwagle/remote-code-bridge` instead.
</details>

### What installation sets up

| Where | What |
|---|---|
| Remote | `~/.local/bin/remote-code-bridge` (a single-file copy of the program), a `~/.local/bin/code` link to it, `~/.config/remote-code-bridge/` (settings and the install record), a socket directory (`~/.cache/remote-code-bridge/`, or under `$XDG_RUNTIME_DIR` or `/tmp`), and a `~/.local/bin` PATH line in your shell startup file. It refuses to replace a `code` command that isn't its own. |
| Your machine | `~/.config/remote-code-bridge/` (settings and the install record), a log directory, and, if you chose it, the login service. The `remote-code-bridge` command itself belongs to uv or pip. |
| Both | A random 64-character token shared by the two sides. It travels to the remote over SSH standard input, never on a command line. |

Re-running `remote-code-bridge install` is always safe: it keeps your token, settings, and service choice.

## Everyday commands

```sh
remote-code-bridge status        # either machine: is the bridge running, is the tunnel up?
remote-code-bridge serve         # run the bridge yourself (if you didn't choose the service)
remote-code-bridge update        # upgrade the package, update the remote, restart the service
remote-code-bridge uninstall     # remove everything it installed, here and on the remote
```

`update` asks PyPI for the latest release and, if it is newer than what you run, installs it with whatever installed it (uv or pip), then re-runs `install` for the saved alias. Prefer it to `uv tool upgrade remote-code-bridge`, which upgrades only your machine: the remote keeps the old version until you run `remote-code-bridge install` again. An installation from the GitHub URL moves to PyPI on its first `update`. On Windows it finishes in a new window, because a running program can't replace its own files there.

`uninstall` lists what it will remove and asks before doing it (`--yes` skips the question). It removes the service, then the remote side over SSH, then your machine's side. If the remote can't be reached it stops and changes nothing; `--host-only` cleans just your machine (then run `~/.local/bin/remote-code-bridge uninstall` on the remote yourself). The program itself is uv's or pip's to remove, and the last step tells you how:

```sh
uv tool uninstall remote-code-bridge
```

**Coming from version 1:** install with `uv tool install --force remote-code-bridge` (`--force` replaces the old `remote-code-bridge` command), then run `remote-code-bridge install`. It keeps your token and removes version 1's `RemoteForward` include from `~/.ssh/config`.

## Troubleshooting

| You see | Do this |
|---|---|
| `no tunnel from your host` on the remote | Run `remote-code-bridge status` on your machine. It must be awake and logged in, and the bridge must be running (the service, or `remote-code-bridge serve`). |
| `tunnel: auth_failed` | The bridge can't log in without a prompt: `ssh-add` your key (Windows: start the OpenSSH Authentication Agent service), then check `ssh -o BatchMode=yes devbox true`. |
| `tunnel: backoff` with a forwarding error | The remote sshd must allow forwarding to Unix sockets (`AllowStreamLocalForwarding yes`, the default; no `DisableForwarding`). |
| `token: rejected` | Run `remote-code-bridge install` again from your machine. |
| `remote-code-bridge: command not found` after `uv tool install` | Run `uv tool update-shell` and open a new terminal. |

Logs: `~/.local/state/remote-code-bridge/bridge.log` (Linux), `~/Library/Logs/remote-code-bridge.log` (macOS), `%LOCALAPPDATA%\remote-code-bridge\bridge.log` (Windows).

## How it works

1. The bridge listens on `127.0.0.1:39731` on your machine and keeps one SSH connection to the remote: `ssh -N -R ~/.cache/remote-code-bridge/bridge.sock:127.0.0.1:39731 devbox`. That puts a Unix socket on the remote, in a directory only you can open, that leads back to the bridge.
2. `code .` on the remote resolves the path, notes whether it is a folder, and sends an authenticated `POST /open` through that socket.
3. The bridge checks the token, alias, path, and flags, then starts `code --folder-uri vscode-remote://ssh-remote+devbox/path` (or `--file-uri`) without a shell.

See [Architecture](https://github.com/kshitizwagle/remote-code-bridge/blob/master/docs/ARCHITECTURE.md) and [Security](https://github.com/kshitizwagle/remote-code-bridge/blob/master/docs/SECURITY.md) for details.

## Development

```sh
uv run --with pytest --with pytest-cov python -m pytest   # unit and integration tests (fake ssh, no network)
uvx ruff check . && uvx ruff format --check .
./scripts/smoke-test.sh                       # the single-file copy sent to remotes: serve + code
./scripts/native-linux-install-update-test.sh # Docker, real sshd: install, sessions, update, uninstall
uv tool install --force .                     # try your working copy as the real command
```

Run the pieces by hand without installing (`REMOTE_CODE_BRIDGE_TUNNEL=0` skips the SSH tunnel; the client then uses TCP `127.0.0.1:PORT`):

```sh
export REMOTE_CODE_BRIDGE_TOKEN="$(python3 -m remote_code_bridge generate-token)"
REMOTE_CODE_BRIDGE_DRY_RUN=1 REMOTE_CODE_BRIDGE_TUNNEL=0 REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox \
  python3 -m remote_code_bridge serve
# another terminal, same token:
REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox python3 -m remote_code_bridge open .
```

Configuration lives in `~/.config/remote-code-bridge/host.env` and `remote.env`; non-empty `REMOTE_CODE_BRIDGE_*` environment variables override them. Don't set a different `REMOTE_CODE_BRIDGE_TOKEN` in your shell for an installed setup; it would override the saved one.

Releases: bump `version` in `pyproject.toml` and `__version__` in `remote_code_bridge/__init__.py` (a test fails if they differ), then push a `v*` tag (or use **Actions → release → Run workflow**). The workflow tests that commit, checks both versions match the tag, builds with `uv build`, publishes to PyPI through trusted publishing, and attaches the wheel and sdist to a GitHub release. Users get it with `remote-code-bridge update`, which only ever installs published releases. To test unreleased changes end to end, run `RCB_PACKAGE_SPEC=/path/to/checkout remote-code-bridge update`.

## License

MIT
