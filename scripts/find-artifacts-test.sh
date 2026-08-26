#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/remote-code-bridge-find-artifacts.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
FAKE_BIN="$TMP/bin"
OUTPUT="$TMP/output"
LOG="$TMP/gh.log"
mkdir -p "$FAKE_BIN"

fail() {
    printf '%s\n' "FAIL: $*" >&2
    exit 1
}

cat >"$FAKE_BIN/gh" <<'EOF'
#!/bin/sh
set -eu

printf '%s\n' "$*" >>"$RCB_FAKE_GH_LOG"
count=$(wc -l <"$RCB_FAKE_GH_LOG")

if [ "${RCB_FAKE_GH_MODE:-wait}" = failure ]; then
    printf '%s\n' '202	completed	failure'
elif [ "$count" -eq 1 ]; then
    # The latest build is still running; an older successful run must not win.
    printf '%s\n' '201	in_progress	'
else
    printf '%s\n' '201	completed	success'
fi
EOF
chmod 755 "$FAKE_BIN/gh"

SHA=0123456789abcdef0123456789abcdef01234567
if ! PATH="$FAKE_BIN:$PATH" \
    SHA="$SHA" \
    GITHUB_OUTPUT="$OUTPUT" \
    RCB_FAKE_GH_LOG="$LOG" \
    RCB_ARTIFACT_LOOKUP_INTERVAL=0 \
    RCB_ARTIFACT_LOOKUP_ATTEMPTS=3 \
    "$ROOT/scripts/find-artifacts.sh" owner/repository release.yml; then
    fail 'finder did not wait for the current build to finish'
fi

[ "$(cat "$OUTPUT")" = 'run_id=201' ] || fail 'finder returned the wrong run id'
[ "$(wc -l <"$LOG")" -eq 2 ] || fail 'finder did not poll the in-progress build'
grep -F -- "--commit $SHA" "$LOG" >/dev/null || fail 'finder did not scope the query to the commit'
grep -F -- '--event workflow_run' "$LOG" >/dev/null || fail 'finder did not scope the query to workflow_run'

: >"$OUTPUT"
: >"$LOG"
if PATH="$FAKE_BIN:$PATH" \
    SHA="$SHA" \
    GITHUB_OUTPUT="$OUTPUT" \
    RCB_FAKE_GH_LOG="$LOG" \
    RCB_FAKE_GH_MODE=failure \
    RCB_ARTIFACT_LOOKUP_INTERVAL=0 \
    RCB_ARTIFACT_LOOKUP_ATTEMPTS=3 \
    "$ROOT/scripts/find-artifacts.sh" owner/repository release.yml >"$TMP/failure.out" 2>&1; then
    fail 'finder accepted a failed build'
fi
grep -F -- 'completed with conclusion failure' "$TMP/failure.out" >/dev/null ||
    fail 'finder did not report the failed build'

printf '%s\n' 'find-artifacts tests passed'
