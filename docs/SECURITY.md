# Security

This project lets a remote SSH session ask your machine to open VS Code on an SSH alias you already use. Keep that path narrow.

## Boundaries

- The host service listens only on `127.0.0.1`. It refuses to start with any other `REMOTE_CODE_BRIDGE_BIND`.
- The remote reaches it only through a reverse tunnel that the host service opens itself. On the remote that tunnel is a Unix socket inside a directory owned by you with mode 0700, so other users on a shared remote can't connect to it or squat on it to collect your token. (Version 1 used TCP port 39731 on the remote's localhost, which any local user could bind first.)
- `POST /open` and `POST /check` require the bearer token, compared in constant time.
- Requests are capped at 64 KiB of body, 16 KiB of headers, and 5 seconds in total.
- The path must be an absolute POSIX path without control characters. The alias must look like a concrete SSH alias (`[A-Za-z0-9][A-Za-z0-9._-]*`, so it can never become an `ssh` option) and be in `REMOTE_CODE_BRIDGE_ALLOWED_HOSTS`.
- Only `--reuse-window`/`-r`, `--new-window`/`-n`, and `--goto`/`-g` are forwarded to VS Code.
- VS Code is started as an argument list, never through a shell. On Windows, when `code` is the `code.cmd` batch launcher, paths containing `" % ! ^ & | < >` are rejected, because `cmd.exe` interprets them even inside quotes.
- Child processes (VS Code, `ssh`, the installer) never inherit `REMOTE_CODE_BRIDGE_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`.

## Tokens

`remote-code-bridge generate-token` prints 32 random bytes as hex. The installer reuses a valid existing host token or generates one, writes `host.env` and `remote.env` readable only by you (mode 0600; on Windows an ACL for your account only), and sends the remote copy over SSH standard input. The token never appears in a command line, URL, filename, or log (the log redacts it).

Treat the token like a password. Don't commit either config file or paste it into issues. To rotate it, delete `REMOTE_CODE_BRIDGE_TOKEN` from `host.env` and re-run the installer.

## Installation source

The package is installed from this GitHub repository over HTTPS (`uv tool install git+https://github.com/kshitizwagle/remote-code-bridge`). To pin a reviewed version, install a tag: `…/remote-code-bridge@v2.0.0`. It has no third-party dependencies. The remote never downloads anything: it receives its copy of the program from your machine over SSH standard input.

`remote-code-bridge update` installs the latest commit of the default branch (or `RCB_PACKAGE_SPEC` if set) with the same tool that installed it.

## Uninstall

Each machine records what installation created (`~/.config/remote-code-bridge/manifest.json`), and `remote-code-bridge uninstall` removes exactly that, including the token files on both machines. It never deletes a directory it didn't create or a file it doesn't own; in shell startup files it removes only its own marked block.

## SSH

- **The bridge connects to the remote with your own SSH configuration and credentials**, without a prompt (`BatchMode=yes`), and keeps that one connection open while it runs (as the login service, or while you run `remote-code-bridge serve`).
- **What the remote sshd must allow:** forwarding to Unix sockets (`AllowStreamLocalForwarding yes`, the default, and no `DisableForwarding`).
- **Your `~/.ssh/config` is not modified.**
- **Helper connections can't be redirected.** Installer probes and the tunnel's preparation step use `ClearAllForwardings=yes` and `ControlPath=none`, so they never request forwards or share a connection with your sessions.

## SSH config discovery

The installer reads `~/.ssh/config` and the files it includes only if each file is owned by you and not writable by other users (on Windows: owner and writers limited to you, SYSTEM, and Administrators, compared by SID). It only parses them. The one directive it refuses is `Match … exec`, because the `ssh -G` calls it makes to compare aliases would run that command. `ProxyCommand` and similar directives are allowed: `ssh -G` doesn't run them, and your own `ssh` already does.

## Threat model

These controls stop network exposure, other local users on the remote, and shell injection. They don't protect against someone who controls your remote account: they can read the token and ask your machine to open VS Code on the allowed aliases. Rotate the token if you suspect that.
