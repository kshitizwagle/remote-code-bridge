#!/bin/sh
# remote-code-bridge installer bootstrap for Linux and macOS hosts.
#
#   curl -fsSL https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.sh | sh -s -- [ssh-alias]
#
# Finds Python 3.8+, downloads remote-code-bridge.pyz, checks its SHA-256, and runs its installer.
# Everything else (SSH discovery, remote install, host service) is in the Python code.
set -eu

# Keep an optional GitHub token out of every child process; it is only used to retry a download.
RCB_GH_TOKEN=${GH_TOKEN:-}
unset GH_TOKEN GITHUB_TOKEN REMOTE_CODE_BRIDGE_TOKEN token 2>/dev/null || :

RELEASE_URL=${RCB_RELEASE_URL:-https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download}
ARCHIVE=remote-code-bridge.pyz

die() { printf 'remote-code-bridge install: %s\n' "$*" >&2; exit 1; }

[ "$#" -le 1 ] || die 'usage: install.sh [ssh-alias]'

PYTHON=
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    PYTHON=$(command -v "$candidate")
    break
  fi
done
[ -n "$PYTHON" ] || die 'Python 3.8 or newer is required.
  macOS:          xcode-select --install   (or: brew install python)
  Debian/Ubuntu:  sudo apt install python3
  Fedora:         sudo dnf install python3'

WORK=$(mktemp -d "${TMPDIR:-/tmp}/remote-code-bridge-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

download() {
  status=$(curl -fsSL --retry 2 --connect-timeout 15 -w '%{http_code}' -o "$2" "$1") && return
  case "$status" in
    403|429)
      # The token goes to curl on stdin (-K -), never on its command line.
      if [ -n "$RCB_GH_TOKEN" ] && ! printf %s "$RCB_GH_TOKEN" | LC_ALL=C grep -q '[[:cntrl:]"\\]' &&
        printf 'header = "Authorization: Bearer %s"\n' "$RCB_GH_TOKEN" |
          curl -q -K - -fsSL --retry 2 --connect-timeout 15 -o "$2" "$1"; then return; fi
      die "download failed for $(basename "$1") with HTTP $status. export GH_TOKEN and retry." ;;
    *) die "download failed for $(basename "$1")${status:+ with HTTP $status}." ;;
  esac
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else "$PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
  fi
}

printf '==> Downloading %s\n' "$ARCHIVE" >&2
download "$RELEASE_URL/$ARCHIVE" "$WORK/$ARCHIVE"
download "$RELEASE_URL/$ARCHIVE.sha256" "$WORK/$ARCHIVE.sha256"
want=$(awk 'NR==1 {print tolower($1)}' "$WORK/$ARCHIVE.sha256")
[ -n "$want" ] && [ "$want" = "$(sha256 "$WORK/$ARCHIVE")" ] || die "checksum failed for $ARCHIVE"

status=0
"$PYTHON" "$WORK/$ARCHIVE" install "$@" || status=$?
exit "$status"
