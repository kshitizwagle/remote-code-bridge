# Build MVP: remote `code .` opens VS Code on host through SSH reverse tunnel

## Problem

In WSL, running `code .` inside the Linux environment opens VS Code on the host. Normal SSH sessions do not have that bridge, so running `code .` on a remote server cannot open the host VS Code client.

## Goal

Create an open-source MVP that lets a user run `code .` on a remote SSH machine and open that folder in VS Code on the host using VS Code Remote-SSH.

## Proposed repository name

`remote-code-bridge`

## User-facing workflow

```bash
ssh devbox
cd ~/project
code .
```

Expected result:

```bash
code --remote ssh-remote+devbox /home/kshitiz/project
```

runs on the host and opens VS Code connected to the remote folder.

## Scope

### Host folder

Create `host/` containing:

1. `remote_code_bridge.py`
   - Python 3 HTTP daemon
   - Binds only to `127.0.0.1`
   - Accepts `POST /open`
   - Requires `Authorization: Bearer <token>`
   - Accepts JSON body with `host`, `path`, and optional `args`
   - Validates path is absolute
   - Optionally validates host alias against an allowlist
   - Runs `code --remote ssh-remote+<host> <path>` without shell interpolation

2. `install.sh`
   - Installs daemon to `~/.local/bin/remote-code-bridge`
   - Creates `~/.config/remote-code-bridge/host.env`
   - Generates a token if missing

3. `requirements.txt`
   - No Python package dependencies
   - Documents external dependencies: `python3`, `code`, `ssh`

4. Optional service examples
   - `remote-code-bridge.service` for Linux systemd user service
   - `com.remote-code-bridge.plist` for macOS launchd

### Remote folder

Create `remote/` containing:

1. `code`
   - Bash wrapper installed at `~/.local/bin/code`
   - Resolves `.` and relative paths to absolute remote paths
   - Reads config from `~/.config/remote-code-bridge/remote.env`
   - Sends authenticated JSON request to `http://127.0.0.1:<port>/open`
   - Supports MVP flags: `--reuse-window`, `-r`, `--new-window`, `-n`, `--goto`, `-g`
   - Rejects unsupported flags clearly

2. `install.sh`
   - Installs wrapper to `~/.local/bin/code`
   - Creates `~/.config/remote-code-bridge/remote.env`
   - Prints PATH instructions

3. `requirements.txt`
   - No Python package dependencies
   - Documents external dependencies: `bash`, `python3`, `realpath`

### Configs folder

Create `configs/` containing:

1. `host.env.example`
   - Bind address
   - Port
   - Token
   - Code binary path
   - Default host alias
   - Allowed hosts
   - Dry-run mode

2. `remote.env.example`
   - Port
   - Host alias
   - Token

3. `ssh_config.example`
   - Shows `RemoteForward 127.0.0.1:39731 127.0.0.1:39731`
   - Includes a normal LAN/IP example
   - Includes a Cloudflare SSH hostname example

## Acceptance criteria

- Running the host daemon with dry-run enabled returns the exact VS Code command it would execute.
- Running the remote wrapper with `code .` sends the current absolute remote path to the host bridge.
- The bridge refuses requests without the correct bearer token.
- The bridge refuses non-absolute paths.
- The bridge never binds to public interfaces by default.
- The repo includes README setup instructions for host and remote machines.
- The repo includes MIT license.
- The repo includes a smoke test script and GitHub Actions workflow.

## Manual test plan

1. On host, run:

   ```bash
   ./host/install.sh
   set -a
   source ~/.config/remote-code-bridge/host.env
   set +a
   remote-code-bridge
   ```

2. In host SSH config, add:

   ```sshconfig
   Host devbox
       HostName 192.168.0.1
       User kshitiz
       RemoteForward 127.0.0.1:39731 127.0.0.1:39731
       ExitOnForwardFailure yes
   ```

3. SSH into remote:

   ```bash
   ssh devbox
   ```

4. On remote, install wrapper:

   ```bash
   ./remote/install.sh
   nano ~/.config/remote-code-bridge/remote.env
   ```

5. Set the same token and host alias.

6. Run:

   ```bash
   cd ~/project
   code .
   ```

7. Verify VS Code opens on host using Remote-SSH.

## Out of scope for MVP

- Windows host support outside WSL
- Full compatibility with every VS Code CLI flag
- Native package managers
- GUI configuration
- Multiple named profiles beyond simple environment configs
