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

wait_for_health() {
    for _ in $(seq 1 50); do
        if python3 - "$PORT" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=0.2)
except Exception:
    raise SystemExit(1)
PY
        then
            return 0
        fi
        sleep 0.1
    done
    echo "remote-code-bridge: host bridge did not become healthy" >&2
    return 1
}
wait_for_health

# REMOTE_CODE_BRIDGE_CONFIG=/dev/null keeps this hermetic: without it, remote/code
# would source ~/.config/remote-code-bridge/remote.env if present (e.g. after
# running remote/install.sh) and silently override the token/host-alias below.
output="$(REMOTE_CODE_BRIDGE_CONFIG=/dev/null \
REMOTE_CODE_BRIDGE_TOKEN="$TOKEN" \
REMOTE_CODE_BRIDGE_PORT="$PORT" \
REMOTE_CODE_BRIDGE_HOST_ALIAS="$HOST_ALIAS" \
"$ROOT_DIR/remote/code" .)"

expected="--remote ssh-remote+${HOST_ALIAS} $(pwd -P)"
if [[ "$output" != *"$expected"* ]]; then
    echo "remote-code-bridge: unexpected dry-run command" >&2
    echo "  expected to contain: $expected" >&2
    echo "  got: $output" >&2
    exit 1
fi

echo "smoke test passed"
