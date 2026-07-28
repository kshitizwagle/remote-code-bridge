# Security

This tool intentionally creates a path for a remote machine to ask your host to open VS Code. Keep that path narrow.

## Defaults

- Host daemon binds to `127.0.0.1` only.
- Remote access happens through SSH `RemoteForward` only.
- Requests require `Authorization: Bearer <token>`.
- The host can restrict allowed SSH aliases with `REMOTE_CODE_BRIDGE_ALLOWED_HOSTS`.
- The host runs the VS Code command using a subprocess argument list, not a shell command string.

## Do not do this

Do not set:

```bash
REMOTE_CODE_BRIDGE_BIND=0.0.0.0
```

That would expose a local command-opening API to your network. Very convenient for attackers, which is usually considered rude.

## Token handling

Generate a token on the host:

```bash
openssl rand -hex 32
```

Use the same token in:

```text
~/.config/remote-code-bridge/host.env
~/.config/remote-code-bridge/remote.env
```

Both files should be mode `0600`.

## SSH server requirements

The remote SSH server must allow TCP forwarding. In `/etc/ssh/sshd_config`, this usually means:

```sshconfig
AllowTcpForwarding yes
```

Then restart SSH on the remote.

## Threat model

This protects against accidental access from other machines on the network. It does not protect you if the remote account itself is compromised and the attacker has your bridge token. In that case, the attacker can ask your host VS Code to open arbitrary paths on that remote SSH alias.

That is still much narrower than arbitrary shell execution on the host, but it is not nothing.
