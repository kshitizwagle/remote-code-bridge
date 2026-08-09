# Native Installer and Update CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the complete current-checkout installer and updater on native Ubuntu and Windows GitHub-hosted runners using loopback release artifacts.

**Architecture:** Ubuntu uses Docker Compose to provide an SSH host and remote Linux container, while the test harness runs the real `install.sh` and updater inside the host container. Windows runs the real `install.ps1` and updater on `windows-2022`, serving release assets from a loopback PowerShell HTTP listener and replacing only the unavoidable Linux SSH endpoint with a test-compiled `ssh.exe`; Scheduled Task operations remain real.

**Tech Stack:** POSIX shell, Docker Compose, Ubuntu 24.04 containers, PowerShell 5.1/7, C# `Add-Type`, GitHub Actions, Rust binaries built from the checkout.

## Global Constraints

- Serve every release asset from `127.0.0.1`; do not call a published GitHub release.
- Keep real installer, checksum, binary updater, and platform service behavior; stub only remote SSH on Windows and the Linux user-service manager.
- Use temporary fixture directories and remove containers, tasks, PATH changes, servers, keys, and files on success or failure.
- Preserve the existing parser, contract, Rust, installer, and smoke checks.
- Run the native harness in smoke CI and in the release workflow before publishing.

---

### Task 1: Add the Ubuntu Compose SSH fixture and host-container harness

**Files:**
- Create: `tests/native/linux/Dockerfile`
- Create: `tests/native/linux/remote-entrypoint.sh`
- Create: `tests/native/linux/compose.yml`
- Create: `tests/native/linux/run-install-update.sh`
- Create: `scripts/native-linux-install-update-test.sh`

**Interfaces:**
- Consumes: `target/debug/remote-code-bridge`, `install.sh`, and `RCB_FIXTURE_DIR` from the orchestrator.
- Produces: exit 0 only after the real POSIX installer and updater complete against the Compose SSH target.

- [ ] **Step 1: Write the failing orchestrator contract**

Create `scripts/native-linux-install-update-test.sh` with `set -eu`, a temporary fixture directory, a `docker compose -p` project name, and an EXIT trap that runs `docker compose down --volumes --remove-orphans` and removes the fixture. Invoke `docker compose ... run --rm host /repo/tests/native/linux/run-install-update.sh` before adding the Compose files.

- [ ] **Step 2: Verify RED**

Run `./scripts/native-linux-install-update-test.sh`. It must fail because the Compose file or host run script does not exist; do not treat this as a product failure.

- [ ] **Step 3: Add the shared Ubuntu image and remote SSH entrypoint**

Use `ubuntu:24.04` with `openssh-client`, `openssh-server`, `curl`, `ca-certificates`, and `python3` installed. `remote-entrypoint.sh` must wait for `/fixture/id_ed25519.pub`, create an unprivileged `rcbremote` account, install that key with mode 0600, generate host keys, disable password authentication, and run `/usr/sbin/sshd -D -e`.

- [ ] **Step 4: Add Compose wiring**

Define `remote` and `host` services from the shared image. Mount the repository read-only at `/repo` and the temporary fixture at `/fixture` into both services. Start `remote` first; let `host` run the host harness on the default Compose network so the SSH config can use hostname `remote`.

- [ ] **Step 5: Implement the host harness**

In `run-install-update.sh`:

1. Generate an Ed25519 key in `/fixture`, write a mode-0600 SSH config for `devbox` pointing to `remote`/`rcbremote`, and create a fake `code` executable and fake `systemctl` logger in `/fixture/bin`.
2. Stage the current host binary under `remote-code-bridge-x86_64-unknown-linux-musl` and its real SHA-256 file. Stage `install.sh` as the updater payload and append `printf '%s\\n' updated > "$RCB_UPDATE_MARKER"` to that served copy. Start `python3 -m http.server` on `127.0.0.1` from the release directory.
3. Run the repository `install.sh devbox` with an isolated `HOME`, `RCB_SSH_CONFIG`, `RCB_RELEASE_URL`, fixture `PATH`, and marker path. Assert host/remote configs, installed host/remote binaries, preserved 64-character token, managed SSH files, and three initial service-manager calls.
4. Run the installed `remote-code-bridge update devbox` with the same local release URL and marker. Wait for the marker, assert the token is unchanged, assert the remote transfer ran again, and assert the service log contains the second daemon-reload/enable/restart sequence.
5. Stop the HTTP server and fail on any missing assertion; the parent orchestrator handles Compose cleanup.

- [ ] **Step 6: Verify GREEN**

Run `./scripts/native-linux-install-update-test.sh` on a host with Docker Compose. Expected: the real installer and updater complete and the script prints a concise success line.

- [ ] **Step 7: Commit the Linux harness**

```sh
git add tests/native/linux scripts/native-linux-install-update-test.sh
git commit -m "test: exercise Linux installer update over SSH"
```

### Task 2: Add the Windows native installer/update harness

**Files:**
- Create: `scripts/native-windows-install-update-test.ps1`

**Interfaces:**
- Consumes: `target/debug/remote-code-bridge.exe`, `install.ps1`, and a Windows PowerShell 5.1/7 runtime.
- Produces: exit 0 only after real Windows installation, Scheduled Task startup, asynchronous updater completion, and cleanup checks.

- [ ] **Step 1: Write the failing harness contract**

Create the script with strict mode, temporary `$profile`, `$release`, `$fakeBin`, `$marker`, and `$sshLog` paths. Assert before starting that `Get-ScheduledTask -TaskName remote-code-bridge` returns nothing. Add helper functions for `Fail`, `Wait-Path`, and cleanup, then invoke the current installer and updater through the local release URL.

- [ ] **Step 2: Verify RED**

Run `powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/native-windows-install-update-test.ps1` before adding its fake `ssh.exe` and release server. It must fail at the missing staged release/fixture precondition, not silently skip the full flow.

- [ ] **Step 3: Add the local release server and native SSH fixture**

Stage `remote-code-bridge-x86_64-pc-windows-msvc.exe`, a copy under `remote-code-bridge-x86_64-unknown-linux-musl`, both real SHA-256 files, and a served copy of `install.ps1` with a marker write appended. Start a loopback `System.Net.HttpListener` job serving only that directory.

Compile a small console `ssh.exe` with `Add-Type`: return `hostname fake-remote`, `user rcbremote`, and `port 22` for `-G`; return `Linux`/`x86_64` for uname probes; return `/bin/bash` for the shell probe; consume stdin for transfer commands and append command/input metadata to `$sshLog`. It must never contact a network endpoint.

- [ ] **Step 4: Execute and assert the real Windows flow**

Set `USERPROFILE`, `HOME`, `PATH`, `RCB_RELEASE_URL`, `RCB_UPDATE_MARKER`, and `RCB_FAKE_SSH_LOG` to temporary values. Add a `code.cmd` fixture, run `install.ps1 -SshAlias devbox`, read the generated host token, verify the installed executable/config and fake-SSH transfer log, and verify the task principal is `Interactive`/`Limited`.

Run the installed executable's `update devbox`, wait for the appended marker because the Windows updater is detached, assert the token is unchanged, assert the scheduled task still exists with the same principal, and probe `/healthz` on port 39731.

- [ ] **Step 5: Add mandatory cleanup**

In `finally`, stop and unregister only the task created by this harness, restore the exact original user PATH and process environment values, stop/remove the HTTP job, and remove temporary paths. If cleanup fails, preserve the original failure while reporting cleanup diagnostics.

- [ ] **Step 6: Verify GREEN in both Windows shells**

Run the harness once with Windows PowerShell 5.1 and once with PowerShell Core 7. Expected: both execute the complete installer/update flow and leave no scheduled task behind.

- [ ] **Step 7: Commit the Windows harness**

```sh
git add scripts/native-windows-install-update-test.ps1
git commit -m "test: exercise Windows installer update natively"
```

### Task 3: Wire native E2E checks into smoke and release workflows

**Files:**
- Modify: `.github/workflows/smoke.yml`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: the two native harnesses and existing debug/release build steps.
- Produces: required Linux Compose and Windows native install/update checks on pushes, pull requests, and release builds.

- [ ] **Step 1: Add Ubuntu smoke invocation**

After the existing Ubuntu build/install checks, run `./scripts/native-linux-install-update-test.sh`.

- [ ] **Step 2: Add Windows smoke build and invocations**

In the existing `windows-installer` job, build `cargo build --locked`, then run the native harness once with `shell: powershell` and once with `shell: pwsh`, retaining the parser tests.

- [ ] **Step 3: Gate releases**

Run the Linux harness after the existing release Ubuntu installer checks. In the existing Windows release matrix leg, build the debug binary before the parser/native checks and run the native harness in both shells before the release build step. `publish` already depends on the complete matrix.

- [ ] **Step 4: Validate workflow syntax**

Parse both workflow YAML files with the available YAML parser and run `git diff --check`.

- [ ] **Step 5: Commit workflow wiring**

```sh
git add .github/workflows/smoke.yml .github/workflows/release.yml
git commit -m "ci: run native installer update checks"
```

### Task 4: Run the complete verification suite and review

**Files:**
- Test: all native harnesses and existing project checks

- [ ] **Step 1: Run local verification**

Run `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked`, `./scripts/install-test.sh`, `./scripts/windows-install-test.sh`, `./scripts/native-linux-install-update-test.sh`, both native Windows harness commands, and `REMOTE_CODE_BRIDGE_PORT=49731 ./scripts/smoke-test.sh`.

- [ ] **Step 2: Request code and security review**

Review that only the intended SSH/service boundaries are stubbed, loopback servers cannot bind externally, credentials never enter logs, cleanup is unconditional, and release publication remains gated.

- [ ] **Step 3: Fix findings and rerun affected checks**

Apply only scoped fixes, then rerun the failing native harness plus `git diff --check` and relevant Rust/workflow checks.

- [ ] **Step 4: Commit and push the verified implementation**

Use conventional commits, push the branch, and inspect the resulting smoke and release workflow checks. Do not claim completion until both native jobs report success.
