#!/usr/bin/env bash
# Quick check of the release archive: `serve` in dry-run mode, then `code` (a symlink to the archive).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${REMOTE_CODE_BRIDGE_PORT:-39731}"
TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
TMP_DIR="$(mktemp -d)"
cleanup() {
    [[ -n "${SERVER_PID:-}" ]] && kill "$SERVER_PID" >/dev/null 2>&1 || true
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

PYTHONPATH="$ROOT_DIR" python3 -m remote_code_bridge.bundle "$TMP_DIR/remote-code-bridge.pyz" >/dev/null
chmod 755 "$TMP_DIR/remote-code-bridge.pyz"
ln -s remote-code-bridge.pyz "$TMP_DIR/code"

export HOME="$TMP_DIR" REMOTE_CODE_BRIDGE_TOKEN="$TOKEN" REMOTE_CODE_BRIDGE_PORT="$PORT"
REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox REMOTE_CODE_BRIDGE_DRY_RUN=1 REMOTE_CODE_BRIDGE_TUNNEL=0 \
    "$TMP_DIR/remote-code-bridge.pyz" serve 2>"$TMP_DIR/serve.log" &
SERVER_PID=$!
for _ in $(seq 1 50); do
    curl --fail --silent --max-time 1 "http://127.0.0.1:${PORT}/healthz" >/dev/null && break
    sleep 0.1
done

output="$(REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox "$TMP_DIR/code" --reuse-window .)"
expected="dry-run command: code --reuse-window --remote ssh-remote+devbox $(pwd -P)"
if [[ "$output" != "$expected" ]]; then
    echo "remote-code-bridge: unexpected dry-run command" >&2
    echo "  expected: $expected" >&2
    echo "  got:      $output" >&2
    cat "$TMP_DIR/serve.log" >&2
    exit 1
fi
echo "smoke test passed"
