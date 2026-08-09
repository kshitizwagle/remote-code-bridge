# remote-code-bridge

Run `code .` from a Linux machine reached through SSH and open that directory in VS Code on your macOS or Linux host.

```text
remote: code . → SSH reverse tunnel → host bridge → code --remote ssh-remote+alias /remote/path
```

The release contains one Rust binary. It runs as the host service and, when installed remotely as `code`, as the client. There is no Python, Rust toolchain, or other language runtime required after installation.

## Install

Run this on the machine that runs VS Code:

```sh
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s --
```

For a reproducible install, use a versioned release URL and verify its `install.sh.sha256` before running it.

The installer finds concrete aliases from `~/.ssh/config`, recursively follows `Include` files, ignores wildcard and negated `Host` entries, and probes candidates in configuration order. It installs to the first reachable Linux target. Auto-discovery refuses an SSH config it reads when it is not owned by you, is group/world-writable, or contains executable SSH directives. Password-only targets cannot be probed non-interactively. Configure key-based access or explicitly opt in to an alias:

```sh
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s -- devbox
```

The selected alias must be the same alias that VS Code Remote - SSH uses. Passing an alias skips auto-discovery's config inspection and uses your existing SSH setup. The installer needs OpenSSH, `curl`, a SHA-256 utility, and a POSIX shell. The host needs VS Code, its `code` CLI in `PATH`, and the Remote - SSH extension.

### What installation configures

- Downloads host and remote binaries, verifies their SHA-256 files, and transfers the remote binary and its configuration over SSH.
- Generates a token unless a valid existing host token is present. The remote config is sent through SSH standard input, never as a command argument, URL, or filename.
- Installs `~/.local/bin/remote-code-bridge` on both machines and a remote `~/.local/bin/code` link. It refuses to replace an unrelated remote `code` command.
- Adds `~/.local/bin` to the active Zsh, Bash, or Fish startup file, with `.profile` as the fallback.
- Adds a managed include to `~/.ssh/config`; that include configures `RemoteForward 127.0.0.1:39731 127.0.0.1:39731` and `ExitOnForwardFailure yes` for the selected alias.
- Starts the host bridge as a systemd user service on Linux or a launchd agent on macOS. It starts at user login, when a desktop VS Code session is available.

Reconnect to the remote after installation, then run:

```sh
cd ~/project
code .
```

### GitHub rate limits

If the installer reports a GitHub `403` or `429`, create a GitHub token with access to public releases, export it only in your current shell, and retry:

```sh
export GH_TOKEN=github_pat_...
curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s -- [ssh-alias]
```

The installer retries the download with `GH_TOKEN` only after an anonymous failure. Do not put the token in the install URL or commit it to configuration.

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

Pull requests and pushes run those Rust and installer checks, including an 80% line-coverage gate. Publishing a release, or pushing a `v*`/numeric version tag, publishes the four verified platform binaries, their SHA-256 files, and a version-pinned `install.sh`; all workflow actions are pinned to immutable commits.

## License

MIT
