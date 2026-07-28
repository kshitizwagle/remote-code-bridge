#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/remote-code-bridge"
CONFIG_FILE="${CONFIG_DIR}/host.env"

mkdir -p "$BIN_DIR" "$CONFIG_DIR"
install -m 0755 "$ROOT_DIR/host/remote_code_bridge.py" "$BIN_DIR/remote-code-bridge"

if [[ ! -f "$CONFIG_FILE" ]]; then
    token=""
    if command -v openssl >/dev/null 2>&1; then
        token="$(openssl rand -hex 32)"
    else
        token="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
    fi

    sed "s/change-me-generate-with-openssl-rand-hex-32/${token}/" \
        "$ROOT_DIR/configs/host.env.example" > "$CONFIG_FILE"
    chmod 0600 "$CONFIG_FILE"
fi

cat <<MSG
Installed host bridge:
  $BIN_DIR/remote-code-bridge

Host config:
  $CONFIG_FILE

Next:
  1. Edit $CONFIG_FILE and set your SSH alias in REMOTE_CODE_BRIDGE_DEFAULT_HOST.
  2. Start the bridge:

     set -a
     source "$CONFIG_FILE"
     set +a
     remote-code-bridge

  3. Copy REMOTE_CODE_BRIDGE_TOKEN from host.env to the remote config.
MSG
