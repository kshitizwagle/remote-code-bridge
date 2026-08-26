[![release](https://github.com/kshitizwagle/remote-code-bridge/actions/workflows/release.yml/badge.svg)](https://github.com/kshitizwagle/remote-code-bridge/actions/workflows/release.yml)

---

# remote-code-bridge

Run `code .` from a Linux machine reached through SSH and open that directory in VS Code on your macOS, Linux, or Windows host.

```text
remote: code . → SSH reverse tunnel → host bridge → code --remote ssh-remote+alias /remote/path
```

The release contains one Rust binary. It runs as the host service and, when installed remotely as `code`, as the client. There is no Python, Rust toolchain, or other language runtime required after installation.

## Install

Run this on the machine that runs VS Code:

```sh
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s --
```

On Windows PowerShell, run:

```powershell
irm https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.ps1 | iex
```

For an explicit Windows alias, set it before invoking the downloaded script:

```powershell
$env:RCB_SSH_ALIAS = 'devbox'
irm https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.ps1 | iex
```

The Windows installer uses PowerShell, the built-in OpenSSH client, and a per-user Scheduled Task; it supports x64 Windows (including ARM64 systems with x64 emulation), and VS Code’s `code` command must be available in `PATH`.

For a reproducible install, use a versioned release URL and verify its matching `install.sh.sha256` or `install.ps1.sha256` before running it.

### Updating

Update an existing installation from the host with:

```sh
remote-code-bridge update
```

It reads the saved SSH alias, downloads and verifies the current host and remote binaries, preserves a valid token and custom settings, refreshes the remote wrapper, and restarts the host service. Use `remote-code-bridge update <ssh-alias>` if the saved host config is missing or stale. Rerunning the same installer remains an equivalent recovery path.

The installer finds concrete aliases from `~/.ssh/config`, recursively follows `Include` files, ignores wildcard and negated `Host` entries, and probes candidates in configuration order. It installs to the first reachable Linux target, then applies the same tunnel to every configured alias resolving to that target (matching effective hostname, user, and port). One canonical alias is used for the VS Code target. Discovery refuses an SSH config it reads when it is not owned by you, is group/world-writable, or contains executable SSH directives. Password-only targets cannot be probed non-interactively. Configure key-based access or explicitly opt in to an alias:

```sh
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s -- devbox
```

The selected alias is the canonical VS Code Remote - SSH target; equivalent aliases share its tunnel. An explicit alias still uses the same safe config inspection so equivalent aliases can be discovered. The installer needs OpenSSH, `curl`, a SHA-256 utility, and a POSIX shell. The host needs VS Code, its `code` CLI in `PATH`, and the Remote - SSH extension.

### What installation configures

- Downloads host and remote binaries, verifies their SHA-256 files, and transfers the remote binary and its configuration over SSH.
- Generates a token unless a valid existing host token is present. The remote config is sent through SSH standard input, never as a command argument, URL, or filename.
- Installs `~/.local/bin/remote-code-bridge` on both machines and a remote `~/.local/bin/code` link. It refuses to replace an unrelated remote `code` command.
- Adds `~/.local/bin` to the active Zsh, Bash, or Fish startup file, with `.profile` as the fallback.
- Adds a managed include to `~/.ssh/config`; that include configures `RemoteForward 127.0.0.1:39731 127.0.0.1:39731`, fails closed when forwarding cannot start, uses SSH keepalives to release dead sessions after about 45 seconds, and (on POSIX hosts) sets `ControlMaster auto` with a per-target `ControlPath` so a second connection to an alias multiplexes through the existing session instead of requesting a new reverse forward, for every equivalent alias.
- Starts the host bridge as a systemd user service on Linux, a launchd agent on macOS, or a per-user Scheduled Task on Windows. It starts at user login, when a desktop VS Code session is available.

Reconnect to the remote after installation, then run:

```sh
cd ~/project
code .
```

Only one SSH connection to a target can own the fixed reverse-forward port at a time. On POSIX hosts the managed config now sets `ControlMaster auto`, so a second connection to the same alias (for example, a VS Code Remote-SSH reconnect or a second window) multiplexes through the existing session instead of requesting a new reverse forward, which prevents most conflicts outright. A cleanly closed session still releases the port immediately; a dead network session is still detected after about 45 seconds. If SSH still reports `remote port forwarding failed for listen port 39731`, run `ssh -O exit <alias>` to close the stale master — the managed config always defines `ControlPath` now, so this works. On Windows hosts, where this multiplexing isn't configured, close the old terminal instead, or find the exact client with `pgrep -af 'ssh.*<alias>'` (or Task Manager) and terminate it.

### GitHub rate limits

If the installer reports a GitHub `403` or `429`, create a GitHub token with access to public releases, export it only in your current shell, and retry:

```sh
export GH_TOKEN=github_pat_...
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s -- [ssh-alias]
```

The installer retries the download with `GH_TOKEN` only after an anonymous failure. Do not put the token in the install URL or commit it to configuration.

PowerShell uses the equivalent process-local variable:

```powershell
$env:GH_TOKEN = 'github_pat_...'
irm https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.ps1 | iex
```

## How it works

1. The SSH connection supplies a reverse tunnel from remote `127.0.0.1:39731` to the host bridge on the same address.
2. The remote `code` client resolves one local path and sends an authenticated `POST /open` request through that tunnel.
3. The host validates the token, SSH alias, path, and supported VS Code flags, then invokes the local VS Code CLI without a shell.

The host bridge offers an unauthenticated `GET /healthz` endpoint and an authenticated `POST /open` endpoint. See [Architecture](docs/ARCHITECTURE.md) and [Security](docs/SECURITY.md) for the protocol and limits.

## Development

Build locally with Rust:

```sh
cargo build --release
```

Run the host service with generated or environment-based configuration:

```sh
export REMOTE_CODE_BRIDGE_TOKEN="$(target/release/remote-code-bridge generate-token)"
REMOTE_CODE_BRIDGE_TOKEN="$REMOTE_CODE_BRIDGE_TOKEN" \
REMOTE_CODE_BRIDGE_DRY_RUN=1 \
target/release/remote-code-bridge serve
```

In another terminal, set the same generated token, then invoke the client:

```sh
REMOTE_CODE_BRIDGE_TOKEN="$REMOTE_CODE_BRIDGE_TOKEN" \
REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox \
target/release/remote-code-bridge open .
```

Configuration files are read from `~/.config/remote-code-bridge/host.env` and `remote.env`; non-empty `REMOTE_CODE_BRIDGE_*` environment variables take precedence. `remote-code-bridge generate-token` prints a new token for development or recovery.

Run the project checks with:

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
./scripts/smoke-test.sh
```

Pull requests and pushes run those Rust and installer checks, including an 80% line-coverage gate. Publishing a release, pushing a `v*`/numeric version tag, or choosing **Actions -> release -> Run workflow** publishes the five verified platform binaries, their SHA-256 files, and version-pinned `install.sh`/`install.ps1` installers. The manual workflow asks for a release number and builds from `main`; the `v` prefix is optional. Workflow actions use readable major-version references.

The Linux and macOS assets are native executables and intentionally have no filename extension; the Windows asset is the `.exe` build.

## License

MIT
