# remote-code-bridge

Run `code .` on a remote SSH machine and open the matching folder in VS Code on your host.

This recreates the WSL-style workflow for normal SSH sessions:

```text
remote shell: code .
        ↓
SSH reverse tunnel to host localhost
        ↓
host bridge runs: code --remote ssh-remote+<host-alias> <remote-path>
```

## Status

MVP scaffold intended for open-source development. It is intentionally small: one host daemon, one remote `code` wrapper, and example configs.

## Requirements

### Host machine

- macOS or Linux
- Python 3.9+
- OpenSSH client
- VS Code installed
- `code` command available in `PATH`
- VS Code **Remote - SSH** extension installed

### Remote machine

- Linux shell environment
- Bash
- Python 3.8+
- OpenSSH server with TCP forwarding enabled

## Quick start

Use the same host alias that works with VS Code Remote-SSH. In examples below, the SSH alias is `devbox`.

### 1. Install host bridge

On your host:

```bash
./host/install.sh
```

This installs:

```text
~/.local/bin/remote-code-bridge
~/.config/remote-code-bridge/host.env
```

Start the host bridge:

```bash
set -a
source ~/.config/remote-code-bridge/host.env
set +a
remote-code-bridge
```

Keep it running while testing.

### 2. Add SSH reverse forwarding

Add this to your host `~/.ssh/config`:

```sshconfig
Host devbox
    HostName 192.168.0.1
    User kshitiz
    RemoteForward 127.0.0.1:39731 127.0.0.1:39731
    ExitOnForwardFailure yes
```

Replace `HostName` and `User` with your real server details. If you use Cloudflare SSH routing, `HostName ssh.example.com` is fine as long as VS Code Remote-SSH can connect to the same alias.

Connect:

```bash
ssh devbox
```

### 3. Install remote wrapper

On the remote machine:

```bash
./remote/install.sh
```

Edit the remote config:

```bash
nano ~/.config/remote-code-bridge/remote.env
```

Set:

```bash
REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox
REMOTE_CODE_BRIDGE_TOKEN=<same-token-from-host.env>
```

Make sure `~/.local/bin` comes before other `code` binaries:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Now test from the remote SSH shell:

```bash
cd ~/some-project
code .
```

VS Code should open on your host through Remote-SSH.

## Repository layout

```text
remote-code-bridge/
├── host/
│   ├── remote_code_bridge.py
│   ├── install.sh
│   ├── requirements.txt
│   ├── remote-code-bridge.service
│   └── com.remote-code-bridge.plist
├── remote/
│   ├── code
│   ├── install.sh
│   └── requirements.txt
├── configs/
│   ├── host.env.example
│   ├── remote.env.example
│   └── ssh_config.example
├── docs/
│   ├── ARCHITECTURE.md
│   └── SECURITY.md
├── scripts/
│   └── smoke-test.sh
├── linear-ticket.md
├── LICENSE
└── README.md
```

## How it works

1. Your SSH connection creates a reverse tunnel:

   ```text
   remote 127.0.0.1:39731 → host 127.0.0.1:39731
   ```

2. The remote `code` wrapper sends a JSON request to `127.0.0.1:39731`.
3. The host daemon validates the bearer token.
4. The host daemon runs:

   ```bash
   code --remote ssh-remote+devbox /remote/path
   ```

## Security defaults

- The host bridge binds only to `127.0.0.1`.
- Requests require a bearer token.
- The remote server reaches the bridge only through your SSH reverse tunnel.
- Commands are executed without shell interpolation.
- The host can restrict accepted SSH aliases using `REMOTE_CODE_BRIDGE_ALLOWED_HOSTS`.

## Development

Run the host daemon locally:

```bash
set -a
source configs/host.env.example
set +a
python3 host/remote_code_bridge.py
```

In another terminal:

```bash
REMOTE_CODE_BRIDGE_TOKEN=change-me \
REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox \
remote/code .
```

Without a tunnel, this only works when the wrapper can reach the host daemon directly at local port `39731`.

## Roadmap

- Native installer package
- Launchd and systemd install helpers
- Better support for `code -g file:line:column`
- Multiple named host profiles
- Optional Unix socket mode for same-machine development
- Tests around argument parsing and request validation

## License

MIT
