#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${REMOTE_CODE_BRIDGE_PORT:-39731}"
TEST_TOKEN="${REMOTE_CODE_BRIDGE_TOKEN:-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef}"
HOST_ALIAS="${REMOTE_CODE_BRIDGE_HOST_ALIAS:-devbox}"
TMP_DIR="$(mktemp -d)"
BIN="$ROOT_DIR/target/debug/remote-code-bridge"
CODE="$TMP_DIR/code"

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

cargo build --locked --quiet --manifest-path "$ROOT_DIR/Cargo.toml"
ln -s "$BIN" "$CODE"

REMOTE_CODE_BRIDGE_TOKEN="$TEST_TOKEN" \
REMOTE_CODE_BRIDGE_PORT="$PORT" \
REMOTE_CODE_BRIDGE_DEFAULT_HOST="$HOST_ALIAS" \
REMOTE_CODE_BRIDGE_ALLOWED_HOSTS="$HOST_ALIAS" \
REMOTE_CODE_BRIDGE_DRY_RUN=1 \
"$BIN" serve >/tmp/remote-code-bridge-smoke.log 2>&1 &
SERVER_PID=$!

wait_for_health() {
    for _ in $(seq 1 50); do
        if curl --fail --silent --show-error --max-time 1 "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    echo "remote-code-bridge: host bridge did not become healthy" >&2
    return 1
}
wait_for_health

output="$(REMOTE_CODE_BRIDGE_TOKEN="$TEST_TOKEN" \
REMOTE_CODE_BRIDGE_PORT="$PORT" \
REMOTE_CODE_BRIDGE_HOST_ALIAS="$HOST_ALIAS" \
"$CODE" --reuse-window .)"

expected="--reuse-window --remote ssh-remote+${HOST_ALIAS} $(pwd -P)"
if [[ "$output" != *"$expected"* ]]; then
    echo "remote-code-bridge: unexpected dry-run command" >&2
    echo "  expected to contain: $expected" >&2
    echo "  got: $output" >&2
    exit 1
fi

echo "smoke test passed"
