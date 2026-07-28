#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.config/remote-code-bridge"
CONFIG_FILE="${CONFIG_DIR}/remote.env"

mkdir -p "$BIN_DIR" "$CONFIG_DIR"
install -m 0755 "$ROOT_DIR/remote/code" "$BIN_DIR/code"

if [[ ! -f "$CONFIG_FILE" ]]; then
    cp "$ROOT_DIR/configs/remote.env.example" "$CONFIG_FILE"
    chmod 0600 "$CONFIG_FILE"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) path_hint="already in PATH" ;;
    *) path_hint="add this to your shell config: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

cat <<MSG
Installed remote wrapper:
  $BIN_DIR/code

Remote config:
  $CONFIG_FILE

PATH status:
  $path_hint

Next:
  1. Edit $CONFIG_FILE.
  2. Set REMOTE_CODE_BRIDGE_HOST_ALIAS to your host-side SSH alias, for example devbox.
  3. Set REMOTE_CODE_BRIDGE_TOKEN to the same token as the host config.
  4. Reconnect with SSH reverse forwarding enabled.
  5. Run: code .
MSG
