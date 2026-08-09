# Architecture

`remote-code-bridge` is one Rust multicall binary with two commands:

- `remote-code-bridge serve` runs the host bridge.
- `remote-code-bridge open [code arguments]` runs the remote client. When invoked as `code`, it selects `open` automatically.

## Installation and configuration

The standalone release installer runs on the VS Code host. It downloads a host binary and a matching remote binary, verifies SHA-256 files, then sends the remote binary and remote configuration over separate SSH standard-input streams. The token is not placed in a command argument, URL, filename, or installer output.

Without an argument, the installer recursively reads OpenSSH `Include` files from `~/.ssh/config`, keeps concrete `Host` aliases only, and probes each alias in configuration order with non-interactive `uname` commands. It uses the first reachable Linux alias. For auto-discovery, every config read must be owned by the current user, not group/world-writable, and free of executable SSH directives. An explicit `install.sh alias` bypasses discovery and deliberately opts in to the user's existing SSH configuration.

The installer writes generated configuration files:

```text
host:   ~/.config/remote-code-bridge/host.env
remote: ~/.config/remote-code-bridge/remote.env
```

Both configuration readers use non-empty `REMOTE_CODE_BRIDGE_*` environment variables in preference to file values. This keeps local development and CI configuration-free while normal installations need no manual edits.

The installer also manages an SSH-config include for the selected alias:

```sshconfig
Host devbox
    RemoteForward 127.0.0.1:39731 127.0.0.1:39731
    ExitOnForwardFailure yes
```

It adds `~/.local/bin` to the current shell startup file and installs the host bridge as a systemd user service on Linux or a launchd agent on macOS. The service starts with the user login session; shell startup files only make the local commands discoverable.

## Request flow

```text
remote shell
  |
  | code .
  v
remote-code-bridge open
  |
  | authenticated POST /open to 127.0.0.1:39731
  v
SSH reverse tunnel
  |
  v
remote-code-bridge serve on host
  |
  | validates request and starts an argv-only process
  v
code --remote ssh-remote+devbox /remote/path
```

The reverse tunnel means the remote client never needs a direct connection to the host. The host bridge binds only to `127.0.0.1`.

## HTTP protocol

`GET /healthz` is a small unauthenticated liveness endpoint. The open operation requires:

```http
POST /open
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "host": "devbox",
  "path": "/home/user/project",
  "args": ["--reuse-window"]
}
```

Requests are limited to 64 KiB. The host validates the token, requires an absolute remote path, optionally restricts aliases with `REMOTE_CODE_BRIDGE_ALLOWED_HOSTS`, and preserves only the supported VS Code flags before building the VS Code command as an argument vector.
