#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FIXTURE=$(mktemp -d "${TMPDIR:-/tmp}/remote-code-bridge-native-linux.XXXXXX")
PROJECT="rcb-native-install-$$"
COMPOSE="docker compose -p $PROJECT -f $ROOT/tests/native/linux/compose.yml"

cleanup() {
    $COMPOSE down --volumes --remove-orphans >/dev/null 2>&1 || :
    rm -rf "$FIXTURE"
}
trap cleanup EXIT HUP INT TERM

RCB_FIXTURE_DIR=$FIXTURE; export RCB_FIXTURE_DIR
$COMPOSE run --rm host /repo/tests/native/linux/run-install-update.sh
printf '%s\n' 'native Linux install/update test passed'
