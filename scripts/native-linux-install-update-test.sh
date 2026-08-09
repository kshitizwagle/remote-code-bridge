#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FIXTURE=$(mktemp -d "${TMPDIR:-/tmp}/remote-code-bridge-native-linux.XXXXXX")
PUBLIC=$(mktemp -d "${TMPDIR:-/tmp}/remote-code-bridge-native-linux-public.XXXXXX")
PROJECT="rcb-native-install-$$"
COMPOSE="docker compose -p $PROJECT -f $ROOT/tests/native/linux/compose.yml"
RCB_UID=$(id -u)
RCB_GID=$(id -g)

cleanup() {
    $COMPOSE run --rm --no-deps host /bin/sh -c "chown -R $RCB_UID:$RCB_GID /fixture /public" >/dev/null 2>&1 || :
    $COMPOSE down --volumes --remove-orphans >/dev/null 2>&1 || :
    rm -rf "$FIXTURE"
    rm -rf "$PUBLIC"
}
trap cleanup EXIT HUP INT TERM

RCB_FIXTURE_DIR=$FIXTURE; export RCB_FIXTURE_DIR
RCB_PUBLIC_DIR=$PUBLIC; export RCB_PUBLIC_DIR
export RCB_UID RCB_GID
if ! $COMPOSE run --rm host /repo/tests/native/linux/run-install-update.sh; then
    $COMPOSE logs --no-color remote >&2 || :
    exit 1
fi
printf '%s\n' 'native Linux install/update test passed'
