# Security

This project lets a remote SSH session request that your host opens VS Code on an already configured SSH alias. Keep that path narrow.

## Boundaries

- The host service accepts connections only on `127.0.0.1`.
- The remote reaches it only through the SSH `RemoteForward` installed for aliases resolving to the selected target.
- `POST /open` requires an exact bearer token comparison.
- Requests are capped at 64 KiB and must contain an absolute remote path.
- The host may restrict aliases with `REMOTE_CODE_BRIDGE_ALLOWED_HOSTS`.
- The VS Code command is launched as an argument list, never a shell command string.

Do not change `REMOTE_CODE_BRIDGE_BIND` away from `127.0.0.1`. The bridge rejects non-localhost binding rather than exposing a command-opening endpoint to the network.

## Tokens

`remote-code-bridge generate-token` generates a 32-byte hexadecimal token. The installer reuses a valid existing host token or generates one, writes host and remote configuration with mode `0600`, and transfers the remote configuration over SSH standard input. It does not expose the token in command arguments, URLs, filenames, or normal installer output.

Treat the token like a password. Do not commit either configuration file, paste the token into issue reports, or place it in a GitHub download URL. If an anonymous release download is rate-limited, export `GH_TOKEN` in the current shell and retry the installer; it is used only for the authenticated retry.

The convenience commands use the mutable `latest` release. The PowerShell `irm ... | iex` form executes the downloaded script in the current session; for a reproducible supply-chain check, download a versioned `install.sh` or `install.ps1` and its matching checksum, verify it, then run the installer.

`remote-code-bridge update` downloads the latest installer and runs it with the saved SSH alias. It uses `curl` on POSIX hosts or built-in PowerShell on Windows; if GitHub rate-limits the download, export `GH_TOKEN` and retry.

## SSH requirements

The remote SSH server must permit TCP forwarding. In `sshd_config` this normally requires:

```sshconfig
AllowTcpForwarding yes
```

The managed client configuration adds:

```sshconfig
RemoteForward 127.0.0.1:39731 127.0.0.1:39731
ExitOnForwardFailure yes
ServerAliveInterval 15
ServerAliveCountMax 3
ControlMaster auto
ControlPath ~/.ssh/remote-code-bridge/%C
```

`ExitOnForwardFailure` prevents a login that appears healthy but cannot reach the host bridge.
`ServerAliveInterval` and `ServerAliveCountMax` make the SSH client close an unresponsive session, releasing its remote listener after roughly 45 seconds.
`ControlMaster auto` and `ControlPath ~/.ssh/remote-code-bridge/%C` (POSIX hosts only) let a later connection to the same alias multiplex through an already-open master instead of requesting a second, conflicting reverse forward, and make `ssh -O exit <alias>` a reliable way to release a stale master on demand. The shortened path keeps the Unix-domain control socket within macOS's path limit.

## SSH discovery trust boundary

Alias discovery reads `~/.ssh/config` and recursively included files only when each file is owned by the current user, is not group/world-writable, and has no executable SSH directives (`Match exec`, `ProxyCommand`, `KnownHostsCommand`, `LocalCommand`, `PKCS11Provider`, or `SecurityKeyProvider`). This avoids executing configuration-controlled commands merely to discover equivalent aliases. Explicit aliases use the same fail-closed inspection.

## Threat model

These controls limit accidental network access and shell injection. They do not protect against a compromised remote account that has the bridge token and can use the approved SSH alias: that account can request VS Code opens on that remote. Rotate the token by replacing both generated configuration files and restarting the host user service if you suspect exposure.
