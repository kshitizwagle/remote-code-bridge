#!/bin/sh
# Build dist/remote-code-bridge.pyz (+ .sha256). Usage: scripts/build.sh [version]
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT/dist"
PYTHONPATH=$ROOT python3 -m remote_code_bridge.bundle "$ROOT/dist/remote-code-bridge.pyz" ${1:+"$1"} >/dev/null
cd "$ROOT/dist"
if command -v sha256sum >/dev/null 2>&1; then sha256sum remote-code-bridge.pyz; else shasum -a 256 remote-code-bridge.pyz; fi \
    > remote-code-bridge.pyz.sha256
printf 'built %s\n' "$ROOT/dist/remote-code-bridge.pyz"
