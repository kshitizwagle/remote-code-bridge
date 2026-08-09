#!/usr/bin/env bash
set -euo pipefail

ROOT=/repo
FIXTURE=/fixture
HOME_DIR=$FIXTURE/home
RELEASE=$FIXTURE/release
BIN_DIR=$FIXTURE/bin
SSH_CONFIG=$HOME_DIR/.ssh/config
SERVICE_LOG=$FIXTURE/service.log
MARKER=$FIXTURE/update.marker
SERVER_LOG=$FIXTURE/http.log

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "missing $1"; }
assert_contains() { grep -F -- "$2" "$1" >/dev/null || fail "$1 lacks $2"; }
assert_remote() { ssh devbox "$1" >/dev/null 2>&1 || fail "remote assertion failed: $1"; }

rm -rf "$HOME_DIR" "$RELEASE" "$BIN_DIR" "$SERVICE_LOG" "$MARKER" "$SERVER_LOG"
mkdir -p "$HOME_DIR/.ssh" "$RELEASE" "$BIN_DIR"

ssh-keygen -q -t ed25519 -N '' -f "$FIXTURE/id_ed25519"
chmod 600 "$FIXTURE/id_ed25519"
cp "$FIXTURE/id_ed25519.pub" /public/id_ed25519.pub
chmod 644 /public/id_ed25519.pub
cat >"$SSH_CONFIG" <<EOF
Host devbox
    HostName remote
    User rcbremote
    IdentityFile $FIXTURE/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
EOF
chmod 600 "$SSH_CONFIG"

cat >"$BIN_DIR/code" <<'EOF'
#!/bin/sh
exit 0
EOF
cat >"$BIN_DIR/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"${RCB_SERVICE_LOG:?}"
EOF
chmod 755 "$BIN_DIR/code" "$BIN_DIR/systemctl"

cp "$ROOT/target/debug/remote-code-bridge" "$RELEASE/remote-code-bridge-x86_64-unknown-linux-musl"
chmod 755 "$RELEASE/remote-code-bridge-x86_64-unknown-linux-musl"
sha256sum "$RELEASE/remote-code-bridge-x86_64-unknown-linux-musl" \
    >"$RELEASE/remote-code-bridge-x86_64-unknown-linux-musl.sha256"
cp "$ROOT/install.sh" "$RELEASE/install.sh"
cat >>"$RELEASE/install.sh" <<'EOF'
printf '%s\n' updated >"$RCB_UPDATE_MARKER"
EOF

export HOME="$HOME_DIR"
export PATH="$BIN_DIR:$PATH"
export SHELL=/bin/bash
export RCB_SSH_CONFIG="$SSH_CONFIG"
export RCB_RELEASE_URL=http://127.0.0.1:18080
export RCB_SERVICE_LOG="$SERVICE_LOG"
export RCB_UPDATE_MARKER="$MARKER"

python3 -m http.server 18080 --bind 127.0.0.1 --directory "$RELEASE" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
cleanup() {
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM
for _ in $(seq 1 50); do
    curl --fail --silent --max-time 1 http://127.0.0.1:18080/install.sh >/dev/null && break
    sleep 0.1
done
curl --fail --silent --max-time 1 http://127.0.0.1:18080/install.sh >/dev/null || fail 'release server did not start'
for _ in $(seq 1 50); do
    ssh devbox true >/dev/null 2>&1 && break
    sleep 0.1
done
ssh devbox true >/dev/null 2>&1 || fail 'Compose SSH target did not start'

sh "$ROOT/install.sh" devbox >/dev/null
HOST_BIN="$HOME_DIR/.local/bin/remote-code-bridge"
HOST_CONFIG="$HOME_DIR/.config/remote-code-bridge/host.env"
assert_file "$HOST_BIN"
assert_file "$HOST_CONFIG"
assert_file "$HOME_DIR/.ssh/remote-code-bridge/config"
assert_contains "$HOST_CONFIG" 'REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox'
assert_remote 'test -x "$HOME/.local/bin/remote-code-bridge"'
assert_remote 'test -f "$HOME/.config/remote-code-bridge/remote.env"'
TOKEN=$(awk -F= '$1 == "REMOTE_CODE_BRIDGE_TOKEN" { print $2; exit }' "$HOST_CONFIG")
REMOTE_TOKEN=$(ssh devbox 'sed -n "s/^REMOTE_CODE_BRIDGE_TOKEN=//p" "$HOME/.config/remote-code-bridge/remote.env"')
[ "${#TOKEN}" -eq 64 ] || fail 'installer did not generate a 64-character token'
[ "$REMOTE_TOKEN" = "$TOKEN" ] || fail 'installer did not transfer the remote token'
[ "$(wc -l <"$SERVICE_LOG" | tr -d ' ')" -eq 3 ] || fail 'installer did not invoke systemctl three times'

ssh devbox 'rm -f "$HOME/.local/bin/remote-code-bridge" "$HOME/.config/remote-code-bridge/remote.env"'
"$HOST_BIN" update devbox >/dev/null
for _ in $(seq 1 100); do
    [ -f "$MARKER" ] && break
    sleep 0.1
done
[ -f "$MARKER" ] || fail 'updater did not execute the local release installer'
[ "$(cat "$MARKER")" = updated ] || fail 'updater marker was incorrect'
TOKEN_AFTER=$(awk -F= '$1 == "REMOTE_CODE_BRIDGE_TOKEN" { print $2; exit }' "$HOST_CONFIG")
REMOTE_TOKEN_AFTER=$(ssh devbox 'sed -n "s/^REMOTE_CODE_BRIDGE_TOKEN=//p" "$HOME/.config/remote-code-bridge/remote.env"')
[ "$TOKEN_AFTER" = "$TOKEN" ] || fail 'updater did not preserve the host token'
[ "$REMOTE_TOKEN_AFTER" = "$TOKEN" ] || fail 'updater did not recreate the remote config'
assert_remote 'test -x "$HOME/.local/bin/remote-code-bridge"'
assert_remote 'test -f "$HOME/.config/remote-code-bridge/remote.env"'
[ "$(wc -l <"$SERVICE_LOG" | tr -d ' ')" -eq 6 ] || fail 'updater did not rerun the service manager'

printf '%s\n' 'native Linux install/update flow passed'
