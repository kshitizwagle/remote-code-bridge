#!/usr/bin/env bash
# Runs inside the `host` container (see compose.yml) against a real sshd in the `remote` container.
# Covers the whole life cycle the way a user runs it:
#   uv tool install -> install (with and without the login service) -> many sessions, bursts,
#   tunnel recovery -> update -> uninstall, which must leave both home directories as they were.
set -euo pipefail

ROOT=/repo
FIXTURE=/fixture
HOME_DIR=$FIXTURE/home
SRC=$FIXTURE/src
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
tunnel_up() { remote-code-bridge status | grep -q 'tunnel:      up'; }
# Every path under a home directory, with file checksums and link targets.
snapshot_script='cd "$1" && find . -mindepth 1 -not -path "./.ssh*" | sort | while IFS= read -r f; do
  if [ -L "$f" ]; then echo "$f -> $(readlink "$f")"; elif [ -f "$f" ]; then echo "$f $(md5sum <"$f" | cut -c1-32)"; else echo "$f/"; fi
done'
host_snapshot() { sh -c "$snapshot_script" _ "$HOME_DIR"; }
same_as() {  # same_as <description> <before> <after>: exact match, or show the difference and fail
    [ "$2" = "$3" ] || { diff <(printf '%s\n' "$2") <(printf '%s\n' "$3") >&2; fail "uninstall left traces on $1"; }
}
remote_snapshot() { remote "sh -c '$(printf '%s' "$snapshot_script" | sed "s/'/'\\\\''/g")' _ \$HOME; ls -d /tmp/remote-code-bridge-* 2>/dev/null || :"; }

rm -rf "$HOME_DIR" "$SRC" "$BIN_DIR" "$CODE_LOG" "$FIXTURE/uv"
mkdir -p "$HOME_DIR/.ssh" "$BIN_DIR"

# -- fixtures: an SSH key, an ssh config with a version 1 leftover, fake VS Code and systemctl ---
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
  *stop*|*restart*|*disable*) pkill -f 'remote_code_bridge serve' || :; sleep 0.5 ;;
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

# uv keeps its tools and cache outside HOME here, so the home snapshots only show our own traces.
export UV_TOOL_DIR=$FIXTURE/uv/tools UV_TOOL_BIN_DIR=$FIXTURE/uv/bin UV_CACHE_DIR=$FIXTURE/uv/cache
export HOME=$HOME_DIR PATH=$FIXTURE/uv/bin:$BIN_DIR:$PATH SHELL=/bin/bash FIXTURE_LOG=$FIXTURE/serve.log
trap 'pkill -f "remote_code_bridge serve" || :; pkill -f "remote-code-bridge serve" || :' EXIT
wait_for 30 "sshd in the remote container" ssh -o BatchMode=yes devbox true

HOST_BEFORE=$(host_snapshot)
REMOTE_BEFORE=$(remote_snapshot)

# -- install: uv tool install, then set up the remote and the login service --------------------
cp -r "$ROOT" "$SRC"
uv tool install "$SRC" >/dev/null
remote-code-bridge install devbox --service --yes
HOST_ENV=$HOME_DIR/.config/remote-code-bridge/host.env
TOKEN=$(sed -n 's/^REMOTE_CODE_BRIDGE_TOKEN=//p' "$HOST_ENV")
[ "${#TOKEN}" -eq 64 ] || fail "no token in host.env"
grep -qx 'REMOTE_CODE_BRIDGE_ALLOWED_HOSTS=devbox,devbox-again' "$HOST_ENV" || fail "equivalent aliases not grouped"
[ "$(remote 'sed -n "s/^REMOTE_CODE_BRIDGE_TOKEN=//p" ~/.config/remote-code-bridge/remote.env')" = "$TOKEN" ] \
    || fail "remote token differs"
if grep -q 'remote-code-bridge' "$SSH_CONFIG"; then fail "version 1 include block was not removed"; fi
[ ! -e "$HOME_DIR/.ssh/remote-code-bridge" ] || fail "version 1 managed directory was not removed"
remote '[ "$(readlink ~/.local/bin/code)" = remote-code-bridge ]' || fail "remote code link missing"
printf 'install: ok\n'

# -- many sessions: none of them owns the tunnel, so every one can open VS Code -------------------
for _ in 1 2 3; do ssh devbox 'sleep 300' & done  # terminals you leave open
sleep 1
for i in 1 2 3 4 5 6; do code_on_remote "/srv/session-$i" >/dev/null || fail "code failed in session $i"; done
# A burst of 12 `code` commands at once (from one session: sshd itself refuses >10 simultaneous logins).
remote 'for i in $(seq 1 12); do ~/.local/bin/code /srv/burst-$i >/dev/null & done; wait'
wait_for 20 "18 VS Code launches" sh -c "[ \$(wc -l <'$CODE_LOG') -eq 18 ]"
grep -qx -- '--file-uri vscode-remote://ssh-remote+devbox/srv/burst-7' "$CODE_LOG" || fail "unexpected code arguments"
printf 'sessions and burst: ok\n'

# -- the tunnel heals itself, whichever end dies --------------------------------------------------
pkill -f '^ssh .*-N .*bridge.sock' || fail "no tunnel process to kill"
wait_for 20 "tunnel after killing host ssh" code_on_remote /srv/after-host-kill
remote 'pkill -u "$(id -u)" -f "sshd: rcbremote" || :' || :
wait_for 30 "tunnel after killing remote sshd" code_on_remote /srv/after-remote-kill
printf 'tunnel recovery: ok\n'

# -- update: a newer package version reaches the host and the remote, keeping the token ----------
sed -i 's/^__version__ = .*/__version__ = "2.0.1"/' "$SRC/remote_code_bridge/__init__.py"
RCB_PACKAGE_SPEC=$SRC remote-code-bridge update
remote-code-bridge --version | grep -q 2.0.1 || fail "host not updated"
remote '~/.local/bin/remote-code-bridge --version' | grep -q 2.0.1 || fail "remote not updated"
[ "$(sed -n 's/^REMOTE_CODE_BRIDGE_TOKEN=//p' "$HOST_ENV")" = "$TOKEN" ] || fail "update changed the token"
wait_for 30 "tunnel after update" code_on_remote /srv/after-update
wait_for 10 "host status up" tunnel_up
printf 'update: ok\n'

# -- uninstall: nothing left on either machine -----------------------------------------------------
pkill -f 'ssh devbox sleep 300' || :
remote-code-bridge uninstall --yes
uv tool uninstall remote-code-bridge >/dev/null
hash -r  # forget bash's cached location of the removed command
[ ! -e "$UV_TOOL_BIN_DIR/remote-code-bridge" ] && ! command -v remote-code-bridge >/dev/null || fail "command still installed"
pgrep -f 'remote_code_bridge serve' >/dev/null && fail "service still running"
same_as "the host" "$HOST_BEFORE" "$(host_snapshot)"
same_as "the remote" "$REMOTE_BEFORE" "$(remote_snapshot)"
printf 'uninstall (service): ok, no traces\n'

# -- without the login service: run `serve` yourself ---------------------------------------------
uv tool install "$SRC" >/dev/null
remote-code-bridge install devbox --no-service --yes  # checks the tunnel with a temporary bridge
[ ! -e "$HOME_DIR/.config/systemd" ] || fail "a service was registered without --service"
remote-code-bridge serve >>"$FIXTURE_LOG" 2>&1 &
wait_for 20 "manually started bridge" code_on_remote /srv/manual
pkill -f 'remote-code-bridge serve'
remote-code-bridge uninstall --yes
uv tool uninstall remote-code-bridge >/dev/null
same_as "the host (no service)" "$HOST_BEFORE" "$(host_snapshot)"
same_as "the remote (no service)" "$REMOTE_BEFORE" "$(remote_snapshot)"
printf 'no-service install and uninstall: ok, no traces\n'
printf 'native Linux end-to-end test passed\n'
