# Architecture

`remote-code-bridge` has two halves.

## Host side

The host runs `remote-code-bridge`, a small Python HTTP daemon bound to `127.0.0.1`.

It accepts:

```http
POST /open
Authorization: Bearer <token>
Content-Type: application/json
```

Body:

```json
{
  "host": "devbox",
  "path": "/home/kshitiz/project",
  "args": ["--reuse-window"]
}
```

The daemon launches:

```bash
code --remote ssh-remote+devbox /home/kshitiz/project
```

The command is built as an argument list, not through shell interpolation.

## Remote side

The remote installs a wrapper named `code` into `~/.local/bin/code`.

When you run:

```bash
code .
```

it resolves the target path and sends the authenticated JSON request to:

```text
http://127.0.0.1:39731/open
```

On the remote, that address is not the remote daemon. It is forwarded back to the host through SSH reverse forwarding.

## SSH tunnel

The important SSH config line is:

```sshconfig
RemoteForward 127.0.0.1:39731 127.0.0.1:39731
```

This means:

```text
remote localhost:39731 -> host localhost:39731
```

The remote wrapper never needs direct network access to your laptop.

## Why this resembles WSL

WSL exposes the host VS Code CLI inside the guest. This project gives a normal SSH session an explicit communication path back to the host through the SSH connection you already opened.

## Sequence

```text
User on remote
  |
  | code .
  v
remote wrapper ~/.local/bin/code
  |
  | POST /open through localhost:39731
  v
SSH reverse tunnel
  |
  v
host daemon remote-code-bridge
  |
  | validates token and host alias
  v
host VS Code CLI
  |
  | code --remote ssh-remote+devbox /remote/path
  v
VS Code opens remote folder
```
