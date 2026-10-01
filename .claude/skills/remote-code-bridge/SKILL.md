---
name: remote-code-bridge-conventions
description: Development conventions for remote-code-bridge, a standard-library-only Python project (3.8+) with conventional commits.
---

# Remote Code Bridge Conventions

## Overview

`remote-code-bridge` lets `code .` on an SSH remote open VS Code on the user's own machine. One Python package, `remote_code_bridge/`, is shipped as a single zipapp, `remote-code-bridge.pyz`. Read `docs/ARCHITECTURE.md` before changing behaviour, and `docs/SECURITY.md` before touching validation, tokens, or SSH options.

## Hard rules

- **Standard library only**, and it must run on **Python 3.8**: use `from __future__ import annotations`, and no `match` statements or 3.9+ APIs at runtime. CI runs the tests on 3.8.
- Every `ssh` call goes through `remote_code_bridge/ssh.py`. Helper connections keep `NO_MULTIPLEX` + `NO_FORWARDS`; the tunnel connection must *not* use `ClearAllForwardings` (it would drop its own `-R`).
- Never put the token on a command line, in a URL, a filename, or a log. Scrub `SECRET_ENV` from child environments.
- Commands sent to the remote must survive any login shell (bash, zsh, fish): wrap scripts with `ssh.sh(...)` and avoid backslashes, or send base64 like `install.REMOTE_BOOTSTRAP`.
- User-facing errors are `BridgeError("plain sentence")`; the CLI prints `remote-code-bridge: <message>` and exits 2.

## Layout

| Path | Purpose |
|---|---|
| `remote_code_bridge/protocol.py`, `server.py`, `httpio.py` | host HTTP service |
| `remote_code_bridge/tunnel.py` | the reverse-tunnel supervisor |
| `remote_code_bridge/client.py` | the remote `code` command |
| `remote_code_bridge/install.py`, `remote_setup.py`, `sshconfig.py`, `service.py`, `update.py` | installation |
| `install.sh`, `install.ps1` | small bootstraps: find Python, download and verify the `.pyz`, run `install` |
| `tests/` | pytest; `tests/fakes/fake_ssh.py` stands in for ssh (selected with `RCB_SSH`) |
| `tests/native/linux/` | Docker end-to-end test against a real sshd |

## Checks

```sh
python3 -m pytest --cov=remote_code_bridge --cov-fail-under=80
ruff check . && ruff format --check .
./scripts/smoke-test.sh
./scripts/native-linux-install-update-test.sh   # needs Docker
```

Add a regression test for every bug fix. Installer behaviour is tested end to end in `tests/test_install.py`, by running the real installer against the fake ssh with a separate "remote" HOME.

## Commits

Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`), imperative mood, a short first line, for example:

```text
fix: skip SSH aliases that ssh -G cannot resolve
feat: keep one reverse tunnel per remote in the host service
```
