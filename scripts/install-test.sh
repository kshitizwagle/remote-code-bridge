#!/bin/sh
# Hermetic behavioural checks for the release installer.  No real SSH, HOME,
# service manager, or network command is used.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/remote-code-bridge-install.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
BIN="$TMP/bin"
HOME_DIR="$TMP/home"
REMOTE="$TMP/remote"
REMOTE_HOME="$TMP/remote-home"
mkdir -p "$BIN" "$HOME_DIR/.ssh" "$REMOTE" "$REMOTE_HOME"

fail() { printf '%s\n' "FAIL: $*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "missing $1"; }
assert_contains() { grep -F -- "$2" "$1" >/dev/null || fail "$1 lacks $2"; }
assert_not_contains() { ! grep -F -- "$2" "$1" >/dev/null || fail "$1 contains secret"; }

cat >"$BIN/curl" <<'EOF'
#!/bin/sh
set -eu
if [ "${RCB_FAKE_CURL_FAIL:-}" = 1 ]; then printf '%s' "${RCB_FAKE_CURL_STATUS:-000}"; exit 22; fi
out=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out=$2; shift 2 ;;
        http*) url=$1; shift ;;
        *) shift ;;
    esac
done
[ -n "$out" ] || exit 2
printf '%s\n' "$url" >>"$RCB_FAKE_CURL_LOG"
case "$url" in
    *.sha256)
        case "${RCB_FAKE_BAD_CHECKSUM:-}" in 1) sum=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb;; *) sum=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa;; esac
        printf '%s  %s\n' "$sum" "${url##*/}" | sed 's/\.sha256$//' >"$out" ;;
    *remote-code-bridge-*) cat >"$out" <<'BIN'
#!/bin/sh
case "${1:-}" in
  generate-token)
    if [ "${RCB_FAKE_INVALID_TOKEN:-}" = 1 ]; then
        printf '%s\n' invalid-token
    else
        printf '%s\n' 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    fi ;;
  *) exit 0 ;;
esac
BIN
        chmod 755 "$out" ;;
    *) exit 2 ;;
esac
EOF
cat >"$BIN/sha256sum" <<'EOF'
#!/bin/sh
printf '%s  %s\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "$1"
EOF
cat >"$BIN/ssh" <<'EOF'
#!/bin/sh
set -eu
[ -z "${GH_TOKEN-}" ] && [ -z "${GITHUB_TOKEN-}" ] && [ -z "${RCB_GH_TOKEN-}" ] && [ -z "${REMOTE_CODE_BRIDGE_TOKEN-}" ] && [ -z "${token-}" ] || { printf '%s\n' 'secret leaked to ssh' >&2; exit 97; }
printf '%s\n' "$*" >>"$RCB_FAKE_SSH_LOG"
alias=
for arg in "$@"; do alias=$arg; done
case "$alias" in *'remote.env.'*) [ "${RCB_FAKE_FAIL_REMOTE_CONFIG:-}" = 1 ] && exit 99;; esac
case "$*" in
  *'.remote-code-bridge.tmp'*|*'.remote.env.tmp'*) exit 98 ;;
  *' -G '*|'-G '* )
    case "$alias" in
      canonical|192.168.1.100|other-name)
        printf '%s\n' 'hostname 192.168.1.100' 'user test-user' 'port 22' ;;
      *) printf '%s\n' "hostname $alias" 'user test-user' 'port 22' ;;
    esac
    exit 0 ;;
  *'uname -s'*'uname -m'*)
    case "$*" in *unreachable*) exit 255;; esac
    printf '%s\n%s\n' Linux "${RCB_FAKE_REMOTE_ARCH:-x86_64}" ;;
  *'printf %s'*) printf '%s' "${RCB_FAKE_REMOTE_SHELL:-/bin/zsh}" ;;
  *) HOME="$RCB_FAKE_REMOTE_HOME" SHELL="$RCB_FAKE_REMOTE_SHELL" sh -c "$alias" ;;
esac
EOF
cat >"$BIN/uname" <<'EOF'
#!/bin/sh
case "$1" in
  -s) printf '%s\n' "${RCB_FAKE_HOST_OS:-Linux}" ;;
  -m) printf '%s\n' "${RCB_FAKE_HOST_ARCH:-x86_64}" ;;
  *) exit 2 ;;
esac
EOF
cat >"$BIN/launchctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$RCB_FAKE_SERVICE_LOG"
EOF
cat >"$BIN/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$RCB_FAKE_SERVICE_LOG"
EOF
cat >"$BIN/code" <<'EOF'
#!/bin/sh
exit 0
EOF
cat >"$BIN/id" <<'EOF'
#!/bin/sh
if [ -n "${RCB_FAKE_ID_UID:-}" ]; then
    [ "$1" = -u ] || exit 2
    printf '%s\n' "$RCB_FAKE_ID_UID"
else
    exec /usr/bin/id "$@"
fi
EOF
chmod 755 "$BIN"/*

run_install() {
    if [ -n "${GH_TOKEN-}" ]; then export GH_TOKEN; fi
    PATH="$BIN:$PATH" HOME="$HOME_DIR" SHELL="${RCB_TEST_SHELL:-/bin/zsh}" \
    RCB_FAKE_SSH_LOG="$TMP/ssh.log" RCB_FAKE_SERVICE_LOG="$TMP/service.log" \
    RCB_FAKE_REMOTE_HOME="$REMOTE_HOME" RCB_FAKE_CURL_LOG="$TMP/curl.log" \
    RCB_FAKE_CURL_FAIL="${RCB_TEST_CURL_FAIL:-}" RCB_FAKE_CURL_STATUS="${RCB_TEST_CURL_STATUS:-}" RCB_FAKE_BAD_CHECKSUM="${RCB_TEST_BAD_CHECKSUM:-}" RCB_FAKE_FAIL_REMOTE_CONFIG="${RCB_TEST_FAIL_REMOTE_CONFIG:-}" RCB_FAKE_UNRELATED_CODE="${RCB_TEST_UNRELATED_CODE:-}" \
    RCB_FAKE_HOST_OS="${RCB_TEST_HOST_OS:-Linux}" RCB_FAKE_HOST_ARCH="${RCB_TEST_HOST_ARCH:-x86_64}" RCB_FAKE_REMOTE_ARCH="${RCB_TEST_REMOTE_ARCH:-x86_64}" \
    RCB_FAKE_ID_UID="${RCB_TEST_ID_UID:-}" RCB_FAKE_INVALID_TOKEN="${RCB_TEST_INVALID_TOKEN:-}" \
    GH_TOKEN="${GH_TOKEN-}" \
    GITHUB_TOKEN="${GITHUB_TOKEN-}" \
    RCB_FAKE_REMOTE_SHELL="${RCB_TEST_REMOTE_SHELL:-/bin/zsh}" \
    RCB_RELEASE_URL=https://example.invalid/releases RCB_SSH_CONFIG="$HOME_DIR/.ssh/config" \
    "$ROOT/install.sh" "$@"
}

printf '%s\n' 'Host chosen' >"$HOME_DIR/ssh-config-target"
ln -s "$HOME_DIR/ssh-config-target" "$HOME_DIR/.ssh/config"
printf '%s\n' '# existing' >"$HOME_DIR/zshrc-target"
ln -s "$HOME_DIR/zshrc-target" "$HOME_DIR/.zshrc"
run_install chosen >"$TMP/explicit.out"
assert_file "$HOME_DIR/.local/bin/remote-code-bridge"
assert_file "$HOME_DIR/.config/remote-code-bridge/host.env"
assert_file "$REMOTE_HOME/.config/remote-code-bridge/remote.env"
assert_file "$REMOTE_HOME/.local/bin/remote-code-bridge"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" "REMOTE_CODE_BRIDGE_DEFAULT_HOST=chosen"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" "REMOTE_CODE_BRIDGE_CODE_BIN=$BIN/code"
assert_contains "$HOME_DIR/.zshrc" 'remote-code-bridge PATH'
 [ -L "$HOME_DIR/.zshrc" ] && [ -L "$HOME_DIR/.ssh/config" ] || fail 'installer replaced a symlinked local config'
assert_contains "$HOME_DIR/zshrc-target" 'remote-code-bridge PATH'
assert_contains "$HOME_DIR/.ssh/config" 'remote-code-bridge/config'
assert_contains "$HOME_DIR/.ssh/remote-code-bridge/config" 'Host chosen'
assert_not_contains "$HOME_DIR/.config/systemd/user/remote-code-bridge.service" 'EnvironmentFile='
assert_contains "$TMP/service.log" --user
assert_contains "$TMP/service.log" 'restart remote-code-bridge.service'
assert_not_contains "$TMP/ssh.log" safe-token
assert_not_contains "$TMP/ssh.log" 'BatchMode=yes -o ConnectTimeout=5 chosen'
[ "$(awk -F= '$1=="REMOTE_CODE_BRIDGE_TOKEN" {print $2}' "$HOME_DIR/.config/remote-code-bridge/host.env")" = "$(awk -F= '$1=="REMOTE_CODE_BRIDGE_TOKEN" {print $2}' "$REMOTE_HOME/.config/remote-code-bridge/remote.env")" ] || fail 'host and remote token differ'
[ "$(stat -c '%a' "$REMOTE_HOME/.config/remote-code-bridge/remote.env")" = 600 ] || fail 'remote config is not 0600'
[ "$(sed -n '1p' "$HOME_DIR/.ssh/config")" = '# >>> remote-code-bridge include >>>' ] || fail 'managed SSH Include is not first'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh"
cat >"$HOME_DIR/.ssh/config" <<'EOF'
Host canonical 192.168.1.100 other-name
    HostName 192.168.1.100
    User test-user
    Port 22
EOF
run_install canonical >"$TMP/equivalent-aliases.out"
assert_contains "$HOME_DIR/.ssh/remote-code-bridge/config" 'Host canonical 192.168.1.100 other-name'
assert_contains "$HOME_DIR/.ssh/remote-code-bridge/config" 'ServerAliveInterval 15'
assert_contains "$HOME_DIR/.ssh/remote-code-bridge/config" 'ServerAliveCountMax 3'
assert_contains "$HOME_DIR/.ssh/remote-code-bridge/config" 'ControlMaster auto'
assert_contains "$HOME_DIR/.ssh/remote-code-bridge/config" 'ControlPath ~/.ssh/remote-code-bridge/sockets/%C'
[ "$(stat -c '%a' "$HOME_DIR/.ssh/remote-code-bridge/sockets")" = 700 ] || fail 'sockets directory is not 0700'
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_DEFAULT_HOST=canonical'
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_ALLOWED_HOSTS=canonical,192.168.1.100,other-name'
assert_contains "$REMOTE_HOME/.config/remote-code-bridge/remote.env" 'REMOTE_CODE_BRIDGE_HOST_ALIAS=canonical'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh"
cat >"$HOME_DIR/.ssh/config" <<'EOF'
Host unreachable *.wild !excluded
Include "conf.d/*.conf"
Include sibling.d/*.conf
EOF
mkdir -p "$HOME_DIR/.ssh/conf.d"
mkdir -p "$HOME_DIR/.ssh/sibling.d"
printf '%s\n' 'Host still-unreachable' >"$HOME_DIR/.ssh/conf.d/first.conf"
printf '%s\n' 'Host reachable' >"$HOME_DIR/.ssh/sibling.d/second.conf"
: >"$TMP/ssh.log"
run_install >"$TMP/discovery.out"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_DEFAULT_HOST=reachable'
assert_contains "$TMP/ssh.log" unreachable
assert_contains "$TMP/ssh.log" reachable

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh"
printf '%s\n' 'Host fishbox' >"$HOME_DIR/.ssh/config"
RCB_TEST_SHELL=/usr/bin/fish RCB_TEST_REMOTE_SHELL=/usr/bin/fish run_install fishbox >"$TMP/fish.out"
assert_file "$HOME_DIR/.config/fish/config.fish"
assert_contains "$HOME_DIR/.config/fish/config.fish" 'contains -- $HOME/.local/bin $PATH; or set -gx PATH $HOME/.local/bin $PATH'
assert_not_contains "$HOME_DIR/.config/fish/config.fish" 'case ":$PATH:"'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh"
if run_install >"$TMP/no-alias.out" 2>&1; then
    fail 'installer accepted a missing SSH config'
fi
assert_contains "$TMP/no-alias.out" 'no concrete SSH Host aliases found'

printf '%s\n' 'Host ratebox' >"$HOME_DIR/.ssh/config"
if RCB_TEST_CURL_FAIL=1 RCB_TEST_CURL_STATUS=404 run_install ratebox >"$TMP/download-404.out" 2>&1; then
    fail 'installer accepted a failed release download'
fi
assert_not_contains "$TMP/download-404.out" 'export GH_TOKEN and retry'
if RCB_TEST_CURL_FAIL=1 RCB_TEST_CURL_STATUS=429 run_install ratebox >"$TMP/rate-limit.out" 2>&1; then
    fail 'installer accepted a rate-limited release download'
fi
assert_contains "$TMP/rate-limit.out" 'export GH_TOKEN and retry'
GH_TOKEN=secret-token
GITHUB_TOKEN=older-secret
export GH_TOKEN
export GITHUB_TOKEN
if RCB_TEST_CURL_FAIL=1 RCB_TEST_CURL_STATUS=429 run_install ratebox >"$TMP/auth-rate-limit.out" 2>&1; then
    fail 'installer accepted a failed authenticated release download'
fi
unset GH_TOKEN
unset GITHUB_TOKEN
assert_not_contains "$TMP/auth-rate-limit.out" 'GitHub token leaked to ssh'

REMOTE_CODE_BRIDGE_TOKEN=environment-secret
export REMOTE_CODE_BRIDGE_TOKEN
if ! run_install ratebox >"$TMP/bridge-token-env.out" 2>&1; then
    fail 'installer forwarded the host bridge token to ssh'
fi
unset REMOTE_CODE_BRIDGE_TOKEN

token=environment-secret
export token
if ! run_install ratebox >"$TMP/lowercase-token-env.out" 2>&1; then
    fail 'installer forwarded the lowercase token variable to ssh'
fi
unset token

if run_install -bad >"$TMP/bad-alias.out" 2>&1; then
    fail 'installer accepted an option-like SSH alias'
fi
assert_contains "$TMP/bad-alias.out" 'SSH alias contains unsupported characters'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh"
printf '%s\n' 'Host unsafe' >"$HOME_DIR/.ssh/config"
chmod 666 "$HOME_DIR/.ssh/config"
if run_install >"$TMP/unsafe-perms.out" 2>&1; then
    fail 'installer accepted a group/world-writable discovery config'
fi
assert_contains "$TMP/unsafe-perms.out" 'group/world-writable SSH config'
chmod 600 "$HOME_DIR/.ssh/config"

printf '%s\n' 'ProxyCommand true' >>"$HOME_DIR/.ssh/config"
if run_install >"$TMP/unsafe-directive.out" 2>&1; then
    fail 'installer accepted an executable SSH directive'
fi
assert_contains "$TMP/unsafe-directive.out" 'executable SSH directive'

asset_case() {
    rm -rf "$HOME_DIR"
    mkdir -p "$HOME_DIR/.ssh"
    printf '%s\n' 'Host mapbox' >"$HOME_DIR/.ssh/config"
    : >"$TMP/curl.log"
    RCB_TEST_HOST_OS="$1" RCB_TEST_HOST_ARCH="$2" RCB_TEST_REMOTE_ARCH="$3" run_install mapbox >"$TMP/map.out"
    assert_contains "$TMP/curl.log" "remote-code-bridge-$4"
}
asset_case Linux x86_64 x86_64 x86_64-unknown-linux-musl
asset_case Linux aarch64 aarch64 aarch64-unknown-linux-musl
asset_case Darwin x86_64 x86_64 x86_64-apple-darwin
asset_case Darwin aarch64 aarch64 aarch64-apple-darwin

rm -rf "$HOME_DIR" "$REMOTE_HOME"
mkdir -p "$HOME_DIR/.ssh" "$REMOTE_HOME"
printf '%s\n' 'Host invalid-token' >"$HOME_DIR/.ssh/config"
if RCB_TEST_INVALID_TOKEN=1 run_install invalid-token >"$TMP/invalid-token.out" 2>&1; then
    fail 'installer accepted an invalid generated token'
fi
assert_contains "$TMP/invalid-token.out" 'token generator returned an invalid token'

rm -rf "$HOME_DIR" "$REMOTE_HOME"
mkdir -p "$REMOTE_HOME"
mkdir -p "$HOME_DIR/.ssh"
printf '%s\n' 'Host checksum' >"$HOME_DIR/.ssh/config"
if RCB_TEST_BAD_CHECKSUM=1 run_install checksum >"$TMP/checksum.out" 2>&1; then
    fail 'installer accepted an invalid checksum'
fi
assert_contains "$TMP/checksum.out" 'checksum failed'

rm -rf "$HOME_DIR" "$REMOTE_HOME"
mkdir -p "$REMOTE_HOME"
mkdir -p "$HOME_DIR/.ssh" "$HOME_DIR/.config/remote-code-bridge"
printf '%s\n' 'Host preserve' >"$HOME_DIR/.ssh/config"
preserved=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
printf 'REMOTE_CODE_BRIDGE_TOKEN=%s\n' "$preserved" >"$HOME_DIR/.config/remote-code-bridge/host.env"
run_install preserve >"$TMP/preserve.out"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" "REMOTE_CODE_BRIDGE_TOKEN=$preserved"
assert_contains "$REMOTE_HOME/.config/remote-code-bridge/remote.env" "REMOTE_CODE_BRIDGE_TOKEN=$preserved"

cp "$BIN/code" "$BIN/custom-code"
printf '%s\n' \
    'REMOTE_CODE_BRIDGE_BIND=127.0.0.1' \
    'REMOTE_CODE_BRIDGE_CODE_BIN='"$BIN"'/custom-code' \
    'REMOTE_CODE_BRIDGE_DRY_RUN=1' \
    "REMOTE_CODE_BRIDGE_TOKEN=$preserved" >"$HOME_DIR/.config/remote-code-bridge/host.env"
run_install preserve >"$TMP/custom-config.out"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_CODE_BIN='
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" "$BIN/custom-code"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_DRY_RUN=1'

rm -f "$REMOTE_HOME/.config/remote-code-bridge/remote.env"
if RCB_TEST_FAIL_REMOTE_CONFIG=1 run_install preserve >"$TMP/partial.out" 2>&1; then
    fail 'installer accepted a failed remote config transfer'
fi
[ ! -e "$REMOTE_HOME/.config/remote-code-bridge/remote.env" ] || fail 'partial transfer created remote config'
mkdir -p "$REMOTE_HOME/.local/bin"
rm -f "$REMOTE_HOME/.local/bin/code"
printf '%s\n' unrelated >"$REMOTE_HOME/.local/bin/code"
if run_install preserve >"$TMP/unrelated.out" 2>&1; then
    fail 'installer replaced an unrelated remote code command'
fi

rm -rf "$HOME_DIR" "$REMOTE_HOME"
mkdir -p "$HOME_DIR/.ssh" "$REMOTE_HOME"
printf '%s\n' 'Host unsafe-owner' >"$HOME_DIR/.ssh/config"
if RCB_TEST_ID_UID=99999 run_install >"$TMP/unowned-config.out" 2>&1; then
    fail 'auto discovery trusted an unowned SSH config'
fi
assert_contains "$TMP/unowned-config.out" 'refusing unowned SSH config'

chmod 666 "$HOME_DIR/.ssh/config"
if run_install >"$TMP/writable-config.out" 2>&1; then
    fail 'auto discovery trusted a writable SSH config'
fi
assert_contains "$TMP/writable-config.out" 'refusing group/world-writable SSH config'
chmod 600 "$HOME_DIR/.ssh/config"

cat >"$HOME_DIR/.ssh/config" <<'EOF'
Match host unsafe-exec exec "true"
Host unsafe-exec
EOF
if run_install >"$TMP/match-exec.out" 2>&1; then
    fail 'auto discovery accepted Match exec'
fi
assert_contains "$TMP/match-exec.out" 'refusing executable SSH directive'

cat >"$HOME_DIR/.ssh/config" <<'EOF'
Match=host unsafe-equals exec="true"
Host unsafe-equals
EOF
if run_install >"$TMP/match-equals-exec.out" 2>&1; then
    fail 'auto discovery accepted Match= exec'
fi
assert_contains "$TMP/match-equals-exec.out" 'refusing executable SSH directive'

cat >"$HOME_DIR/.ssh/config" <<'EOF'
ProxyCommand=nc %h %p
Host unsafe-proxy
EOF
if run_install >"$TMP/proxy-command.out" 2>&1; then
    fail 'auto discovery accepted ProxyCommand with equals syntax'
fi
assert_contains "$TMP/proxy-command.out" 'refusing executable SSH directive'

mkdir -p "$HOME_DIR/.ssh/unsafe"
printf '%s\n' 'ProxyCommand nc %h %p' >"$HOME_DIR/.ssh/unsafe/equal.conf"
cat >"$HOME_DIR/.ssh/config" <<'EOF'
Include=unsafe/equal.conf
Host unsafe-include-equals
EOF
if run_install >"$TMP/include-equals.out" 2>&1; then
    fail 'auto discovery skipped an Include= file'
fi
assert_contains "$TMP/include-equals.out" 'refusing executable SSH directive'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh/includes"
printf '%s\n' 'Host canonical' >"$HOME_DIR/.ssh/shared.conf"
i=1
while [ "$i" -le 129 ]; do
    ln -s ../shared.conf "$HOME_DIR/.ssh/includes/$i.conf"
    i=$((i + 1))
done
printf '%s\n' 'Include includes/*.conf' >"$HOME_DIR/.ssh/config"
run_install >"$TMP/canonical.out"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_DEFAULT_HOST=canonical'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh/includes with space"
printf '%s\n' 'Include "includes with space/quoted.conf"' >"$HOME_DIR/.ssh/config"
printf '%s\n' 'Host quoted' >"$HOME_DIR/.ssh/includes with space/quoted.conf"
run_install >"$TMP/quoted-include.out"
assert_contains "$HOME_DIR/.config/remote-code-bridge/host.env" 'REMOTE_CODE_BRIDGE_DEFAULT_HOST=quoted'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh/deep"
printf '%s\n' 'Include deep/1.conf' >"$HOME_DIR/.ssh/config"
i=1
while [ "$i" -le 17 ]; do
    next=$((i + 1))
    printf 'Include %s.conf\n' "$next" >"$HOME_DIR/.ssh/deep/$i.conf"
    i=$next
done
printf '%s\n' 'Host too-deep' >"$HOME_DIR/.ssh/deep/18.conf"
if run_install >"$TMP/recursion-limit.out" 2>&1; then
    fail 'auto discovery exceeded the Include recursion limit'
fi
assert_contains "$TMP/recursion-limit.out" 'SSH Include recursion exceeds 16 levels'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh/many"
printf '%s\n' 'Include many/*.conf' >"$HOME_DIR/.ssh/config"
i=1
while [ "$i" -le 128 ]; do
    printf '%s\n' '# distinct config' >"$HOME_DIR/.ssh/many/$i.conf"
    i=$((i + 1))
done
if run_install >"$TMP/file-limit.out" 2>&1; then
    fail 'auto discovery exceeded the Include file limit'
fi
assert_contains "$TMP/file-limit.out" 'SSH Include file limit exceeded'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh"
i=1
while [ "$i" -le 257 ]; do
    printf 'Host alias%s\n' "$i" >>"$HOME_DIR/.ssh/config"
    i=$((i + 1))
done
if run_install >"$TMP/alias-limit.out" 2>&1; then
    fail 'auto discovery exceeded the SSH alias limit'
fi
assert_contains "$TMP/alias-limit.out" 'SSH alias limit exceeded'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh/remote-code-bridge"
printf '%s\n' 'Host managed-link' >"$HOME_DIR/.ssh/config"
printf '%s\n' sentinel >"$HOME_DIR/managed-target"
ln -s "$HOME_DIR/managed-target" "$HOME_DIR/.ssh/remote-code-bridge/config"
if run_install managed-link >"$TMP/managed-link.out" 2>&1; then
    fail 'installer followed a managed-file symlink'
fi
[ "$(sed -n '1p' "$HOME_DIR/managed-target")" = sentinel ] || fail 'managed-file symlink target was overwritten'
assert_contains "$TMP/managed-link.out" 'refusing symlink'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh" "$HOME_DIR/managed-dir-target"
printf '%s\n' 'Host managed-dir-link' >"$HOME_DIR/.ssh/config"
ln -s "$HOME_DIR/managed-dir-target" "$HOME_DIR/.ssh/remote-code-bridge"
if run_install managed-dir-link >"$TMP/managed-dir-link.out" 2>&1; then
    fail 'installer followed a managed-directory symlink'
fi
[ ! -e "$HOME_DIR/managed-dir-target/config" ] || fail 'managed-directory symlink target was overwritten'
assert_contains "$TMP/managed-dir-link.out" 'refusing symlink'

rm -rf "$HOME_DIR"
mkdir -p "$HOME_DIR/.ssh/remote-code-bridge" "$HOME_DIR/managed-sockets-target"
printf '%s\n' 'Host managed-sockets-link' >"$HOME_DIR/.ssh/config"
ln -s "$HOME_DIR/managed-sockets-target" "$HOME_DIR/.ssh/remote-code-bridge/sockets"
if run_install managed-sockets-link >"$TMP/managed-sockets-link.out" 2>&1; then
    fail 'installer followed a managed-sockets symlink'
fi
assert_contains "$TMP/managed-sockets-link.out" 'refusing symlink'

printf '%s\n' 'install tests passed'
