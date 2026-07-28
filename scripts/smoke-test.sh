#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${REMOTE_CODE_BRIDGE_PORT:-39731}"
TOKEN="${REMOTE_CODE_BRIDGE_TOKEN:-test-token}"
HOST_ALIAS="${REMOTE_CODE_BRIDGE_HOST_ALIAS:-devbox}"

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

REMOTE_CODE_BRIDGE_TOKEN="$TOKEN" \
REMOTE_CODE_BRIDGE_PORT="$PORT" \
REMOTE_CODE_BRIDGE_DEFAULT_HOST="$HOST_ALIAS" \
REMOTE_CODE_BRIDGE_ALLOWED_HOSTS="$HOST_ALIAS" \
REMOTE_CODE_BRIDGE_DRY_RUN=1 \
python3 "$ROOT_DIR/host/remote_code_bridge.py" >/tmp/remote-code-bridge-smoke.log 2>&1 &
SERVER_PID=$!

sleep 0.5

REMOTE_CODE_BRIDGE_TOKEN="$TOKEN" \
REMOTE_CODE_BRIDGE_PORT="$PORT" \
REMOTE_CODE_BRIDGE_HOST_ALIAS="$HOST_ALIAS" \
"$ROOT_DIR/remote/code" .

echo "smoke test passed"
