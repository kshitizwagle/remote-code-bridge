#!/usr/bin/env bash
# Runs inside the `host` container (see compose.yml) against a real sshd in the `remote` container.
# Covers: bootstrap install, v1 migration, many concurrent sessions, tunnel recovery, update.
set -euo pipefail

ROOT=/repo
FIXTURE=/fixture
HOME_DIR=$FIXTURE/home
RELEASE=$FIXTURE/release
BIN_DIR=$FIXTURE/bin
CODE_LOG=$FIXTURE/code.log
SSH_CONFIG=$HOME_DIR/.ssh/config

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
remote() { ssh devbox "$@"; }
wait_for() {  # wait_for <seconds> <description> <command...>
    local seconds=$1 what=$2
    shift 2
    for _ in $(seq 1 "$((seconds * 10))"); do "$@" >/dev/null 2>&1 && return 0; sleep 0.1; done
    fail "timed out waiting for $what"
}
code_on_remote() { remote "\$HOME/.local/bin/code $*"; }
tunnel_up() { "$HOME_DIR/.local/bin/remote-code-bridge" status | grep -q 'tunnel:      up'; }

rm -rf "$HOME_DIR" "$RELEASE" "$BIN_DIR" "$CODE_LOG"
mkdir -p "$HOME_DIR/.ssh" "$RELEASE" "$BIN_DIR"

# -- fixtures: an SSH key, an ssh config with a v1 leftover, fake VS Code and systemctl ----------
ssh-keygen -q -t ed25519 -N '' -f "$FIXTURE/id_ed25519"
cp "$FIXTURE/id_ed25519.pub" /public/id_ed25519.pub
chmod 644 /public/id_ed25519.pub
mkdir -p "$HOME_DIR/.ssh/remote-code-bridge"
printf 'Host devbox\n    RemoteForward 127.0.0.1:39731 127.0.0.1:39731\n' >"$HOME_DIR/.ssh/remote-code-bridge/config"
{
    printf '%s\n' '# >>> remote-code-bridge include >>>'
    printf 'Include %s\n' "$HOME_DIR/.ssh/remote-code-bridge/config"
    printf '%s\n' '# <<< remote-code-bridge include <<<'
    printf '%s\n' 'Host devbox devbox-again' '    HostName remote' '    User rcbremote'
    printf '    IdentityFile %s\n' "$FIXTURE/id_ed25519"
    printf '%s\n' '    StrictHostKeyChecking no' '    UserKnownHostsFile /dev/null' '    LogLevel ERROR'
} >"$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>%s\n' "$CODE_LOG" >"$BIN_DIR/code"
# A stand-in for `systemctl --user`: "restart" runs the real service command from the unit file.
cat >"$BIN_DIR/systemctl" <<'SYSTEMCTL'
#!/bin/sh
unit="$HOME/.config/systemd/user/remote-code-bridge.service"
case "$*" in
  *stop*|*restart*) pkill -f 'remote-code-bridge.pyz" serve' || :; sleep 0.5 ;;
esac
case "$*" in
  # `exec` the redirections first: otherwise sh keeps a copy of the caller's stdout pipe open
  # and the installer (which captures our output) waits forever for it to close.
  *restart*) command=$(sed -n 's/^ExecStart=//p' "$unit")
             (exec </dev/null >>"$FIXTURE_LOG" 2>&1; eval "exec $command") & ;;
esac
exit 0
SYSTEMCTL
chmod 755 "$BIN_DIR/code" "$BIN_DIR/systemctl"

# -- a local "GitHub release" ----------------------------------------------------------------
build_release() {
    PYTHONPATH=$ROOT python3 -m remote_code_bridge.bundle "$RELEASE/remote-code-bridge.pyz" "$1" >/dev/null
    (cd "$RELEASE" && sha256sum remote-code-bridge.pyz >remote-code-bridge.pyz.sha256)
}
build_release 2.0.0
python3 -m http.server 18080 --bind 127.0.0.1 --directory "$RELEASE" >/dev/null 2>&1 &
HTTP_PID=$!
trap 'kill $HTTP_PID 2>/dev/null || :; pkill -f "remote-code-bridge.pyz\" serve" || :' EXIT

export HOME=$HOME_DIR PATH=$BIN_DIR:$PATH SHELL=/bin/bash FIXTURE_LOG=$FIXTURE/serve.log
export RCB_RELEASE_URL=http://127.0.0.1:18080
wait_for 10 "release server" curl -fs "$RCB_RELEASE_URL/remote-code-bridge.pyz.sha256"
wait_for 30 "sshd in the remote container" ssh -o BatchMode=yes devbox true

# -- install through the bootstrap (this also verifies the tunnel and the remote end to end) ------
sh "$ROOT/install.sh" devbox
HOST_ENV=$HOME_DIR/.config/remote-code-bridge/host.env
TOKEN=$(sed -n 's/^REMOTE_CODE_BRIDGE_TOKEN=//p' "$HOST_ENV")
[ "${#TOKEN}" -eq 64 ] || fail "no token in host.env"
grep -qx 'REMOTE_CODE_BRIDGE_ALLOWED_HOSTS=devbox,devbox-again' "$HOST_ENV" || fail "equivalent aliases not grouped"
[ "$(remote 'sed -n "s/^REMOTE_CODE_BRIDGE_TOKEN=//p" ~/.config/remote-code-bridge/remote.env')" = "$TOKEN" ] \
    || fail "remote token differs"
if grep -q 'remote-code-bridge' "$SSH_CONFIG"; then fail "v1 include block was not removed"; fi
[ ! -e "$HOME_DIR/.ssh/remote-code-bridge" ] || fail "v1 managed directory was not removed"
remote '[ "$(readlink ~/.local/bin/code)" = remote-code-bridge ]' || fail "remote code link missing"
printf 'install: ok\n'

# -- many sessions: none of them owns the tunnel, so every one can open VS Code -------------------
# Long-lived sessions stay open the whole time (like terminals you leave around)...
for i in 1 2 3; do ssh devbox 'sleep 300' & done
sleep 1
# ...while new sessions come and go and use `code`.
for i in 1 2 3 4 5 6; do code_on_remote "/srv/session-$i" >/dev/null || fail "code failed in session $i"; done
# And a burst: 12 `code` processes at the same instant. (One SSH session runs them: sshd itself
# drops more than 10 simultaneous new logins by default, which is not what we are testing.)
remote 'for i in $(seq 1 12); do ~/.local/bin/code /srv/burst-$i >/dev/null & done; wait'
wait_for 20 "18 VS Code launches" sh -c "[ \$(wc -l <'$CODE_LOG') -eq 18 ]"
grep -qx -- '--remote ssh-remote+devbox /srv/burst-7' "$CODE_LOG" || fail "unexpected code arguments"
printf 'concurrent sessions and burst: ok\n'

# -- the tunnel heals itself, whichever end dies --------------------------------------------------
pkill -f '^ssh .*-N .*bridge.sock' || fail "no tunnel process to kill"
wait_for 20 "tunnel after killing host ssh" code_on_remote /srv/after-host-kill
remote 'pkill -u "$(id -u)" -f "sshd: rcbremote" || :' || :
wait_for 30 "tunnel after killing remote sshd" code_on_remote /srv/after-remote-kill
printf 'tunnel recovery: ok\n'

# -- update keeps the token and installs the new version ------------------------------------------
build_release 2.0.1
"$HOME_DIR/.local/bin/remote-code-bridge" update
[ "$(sed -n 's/^REMOTE_CODE_BRIDGE_TOKEN=//p' "$HOST_ENV")" = "$TOKEN" ] || fail "update changed the token"
remote '~/.local/bin/remote-code-bridge --version' | grep -q 2.0.1 || fail "remote not updated"
wait_for 30 "tunnel after update" code_on_remote /srv/after-update
wait_for 10 "host status up" tunnel_up
printf 'update: ok\n'
printf 'native Linux end-to-end test passed\n'
