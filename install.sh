#!/bin/sh
# Standalone release installer: it never builds or downloads anything remotely.
set -e

# Keep the optional GitHub credential out of every child environment (notably
# ssh, whose user configuration may forward arbitrary environment variables).
# `unset` first clears a possible inherited export attribute on the private
# name before assigning the saved token back to it.
RCB_GH_TOKEN_HOLD=$GH_TOKEN
unset GH_TOKEN GITHUB_TOKEN RCB_GH_TOKEN REMOTE_CODE_BRIDGE_TOKEN token
RCB_GH_TOKEN=$RCB_GH_TOKEN_HOLD
unset RCB_GH_TOKEN_HOLD

PROJECT=kshitizwagle/remote-code-bridge
RELEASE_URL=$RCB_RELEASE_URL
[ -n "$RELEASE_URL" ] || RELEASE_URL="https://github.com/$PROJECT/releases/latest/download"
TMPBASE=$TMPDIR
[ -n "$TMPBASE" ] || TMPBASE=/tmp
WORK=$(mktemp -d "$TMPBASE/remote-code-bridge-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

die() { printf '%s\n' "remote-code-bridge install: $*" >&2; exit 1; }
note() { printf '%s\n' "remote-code-bridge install: $*" >&2; }
safe_credential() { ! printf %s "$1" | LC_ALL=C grep -q '[[:cntrl:]]'; }
arch() { case "$1" in amd64) printf x86_64;; arm64) printf aarch64;; *) printf %s "$1";; esac; }
asset() {
  case "$1:$2" in
    Linux:x86_64) printf x86_64-unknown-linux-musl;;
    Linux:aarch64) printf aarch64-unknown-linux-musl;;
    Darwin:x86_64) printf x86_64-apple-darwin;;
    Darwin:aarch64) printf aarch64-apple-darwin;;
    *) die "unsupported $3 platform: $1/$2";;
  esac
}
download() {
  status=$(curl -fsSL --retry 2 --connect-timeout 15 -w '%{http_code}' -o "$2" "$1") && return
  case "$status" in
    403|429)
      if [ -n "$RCB_GH_TOKEN" ] && safe_credential "$RCB_GH_TOKEN" &&
        printf 'header = "Authorization: Bearer %s"\n' "$RCB_GH_TOKEN" |
          curl -q -K - -fsSL --retry 2 --connect-timeout 15 -o "$2" "$1"; then return; fi
      die "download failed for $(basename "$1") with HTTP $status. export GH_TOKEN and retry."
      ;;
    *) die "download failed for $(basename "$1")${status:+ with HTTP $status}." ;;
  esac
}
sum() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | awk '{print $NF}'
  else die 'need sha256sum, shasum, or openssl'; fi
}
fetch() {
  file="$WORK/remote-code-bridge-$1"
  download "$RELEASE_URL/remote-code-bridge-$1" "$file"
  download "$RELEASE_URL/remote-code-bridge-$1.sha256" "$file.sha256"
  want=$(awk 'NR==1 {print $1}' "$file.sha256")
  got=$(sum "$file")
  [ -n "$want" ] && [ "$want" = "$got" ] || die "checksum failed for $(basename "$file")"
  chmod 755 "$file"; printf '%s\n' "$file"
}
atomic() {
  if [ "$4" = follow ]; then destination=$(resolve_link "$2")
  elif [ -L "$2" ]; then die "refusing symlink destination $2"
  else destination=$2; fi
  d=$(dirname "$destination"); b=$(basename "$destination"); mkdir -p "$d"; umask 077
  t=$(mktemp "$d/.$b.XXXXXX"); cat "$1" >"$t"; chmod "$3" "$t"; mv -f "$t" "$destination"
}
resolve_link() {
  path=$1
  hops=0
  while [ -L "$path" ]; do
    hops=$((hops + 1)); [ "$hops" -le 16 ] || die "too many symlinks resolving $1"
    link=$(readlink "$path") || die "cannot resolve symlink $path"
    case "$link" in /*) path=$link;; *) path="$(dirname "$path")/$link";; esac
  done
  printf '%s\n' "$path"
}
value() {
  if [ -f "$2" ]; then
    awk -F= -v key="$1" '$1==key {print substr($0,length(key)+2);exit}' "$2"
  fi
  :
}
valid_token() {
  [ "$(printf %s "$1" | wc -c | tr -d ' ')" = 64 ] || return 1
  case "$1" in *[!0123456789abcdefABCDEF]*) return 1;; esac
}
valid_alias() {
  case "$1" in
    [abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789]|[abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789][abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-]*) ;;
    *) return 1 ;;
  esac
}
add_alias() {
  case "$1" in ''|!*|*\**|*\?*|*\[*) return;; esac
  valid_alias "$1" || return
  grep -Fqx "$1" "$ALIASES" 2>/dev/null || printf '%s\n' "$1" >>"$ALIASES"
}
safe_discovery_config() {
  owner=$(stat -c %u "$1" 2>/dev/null || stat -f %u "$1")
  mode=$(stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1")
  [ "$owner" = "$(id -u)" ] || die "refusing unowned SSH config $1"
  group=$(printf %s "$mode" | sed 's/.*\(..\)$/\1/' | cut -c1)
  other=$(printf %s "$mode" | sed 's/.*\(..\)$/\1/' | cut -c2)
  case "$group$other" in *[2367]*) die "refusing group/world-writable SSH config $1";; esac
  if grep -Eiq '^[[:space:]]*(Match([[:space:]]*=[[:space:]]*|[[:space:]]+)([^[:space:]]+[[:space:]]+)*exec([[:space:]]*=[[:space:]]*|[[:space:]]|$)|(ProxyCommand|KnownHostsCommand|LocalCommand|PKCS11Provider|SecurityKeyProvider)([[:space:]]|=|$))' "$1"; then
    die "refusing executable SSH directive in $1"
  fi
  return 0
}
parse_includes() {
  awk '
    function emit() { if (token != "") { print token; token = "" } }
    {
      quoted = 0
      for (i = 1; i <= length($0); i++) {
        c = substr($0, i, 1)
        if (c == "\"") quoted = !quoted
        else if (c == "\\") exit 2
        else if (!quoted && c ~ /[[:space:]]/) emit()
        else token = token c
      }
      if (quoted) exit 2
      emit()
    }
  '
}
read_include_pattern() (
  pattern=$1; next_depth=$2
  IFS='
'
  for inc in $pattern; do
    unset IFS
    if [ -f "$inc" ]; then read_config "$inc" "$next_depth"; fi
  done
)
read_config() (
  depth=$2
  [ "$depth" -le 16 ] || die 'SSH Include recursion exceeds 16 levels'
  [ -f "$1" ] || return 0
  resolved=$(resolve_link "$1")
  canonical=$(CDPATH= cd -- "$(dirname "$resolved")" && pwd -P)/$(basename "$resolved")
  safe_discovery_config "$canonical"
  grep -Fqx "$canonical" "$SEEN" 2>/dev/null && return
  [ "$(wc -l <"$SEEN" | tr -d ' ')" -lt 128 ] || die 'SSH Include file limit exceeded'
  printf '%s\n' "$canonical" >>"$SEEN"; dir=$(dirname "$canonical")
  while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s\n' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*#.*$//'); [ -n "$line" ] || continue
    key=$(printf '%s\n' "$line" | sed 's/[[:space:]=].*$//' | tr '[:upper:]' '[:lower:]')
    rest=$(printf '%s\n' "$line" | sed 's/^[^[:space:]=]*//;s/^[[:space:]]*//;s/^=//;s/^[[:space:]]*//')
    case "$key" in
      host)
        set -f; set -- $rest; set +f
        for name in "$@"; do [ "$(wc -l <"$ALIASES" | tr -d ' ')" -lt 256 ] || die 'SSH alias limit exceeded'; add_alias "$name"; done
        ;;
      include)
        patterns=$(printf '%s\n' "$rest" | parse_includes) || die "unsupported SSH Include syntax in $canonical"
        [ -n "$patterns" ] || die "empty SSH Include in $canonical"
        oldifs=$IFS; IFS='
'
        for pat in $patterns; do
          IFS=$oldifs
        case "$pat" in '~/'*) pat="$HOME/$(printf %s "$pat" | sed 's|^~/||')";; /*) ;; *) pat="$dir/$pat";; esac
          read_include_pattern "$pat" "$((depth + 1))"
          IFS='
'
        done
        IFS=$oldifs
        ;;
    esac
  done <"$1"
)
probe() {
  if [ "$2" = discovery ]; then
    out=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$1" 'uname -s; uname -m' </dev/null 2>/dev/null) || return 1
  else
    out=$(ssh "$1" 'uname -s; uname -m' </dev/null 2>/dev/null) || return 1
  fi
  REMOTE_OS=$(printf '%s\n' "$out" | sed -n '1p')
  REMOTE_ARCH=$(arch "$(printf '%s\n' "$out" | sed -n '2p')")
  [ "$REMOTE_OS" = Linux ]
}
ssh_identity() {
  out=$(ssh -G "$1" </dev/null 2>/dev/null) || return 1
  host=$(printf '%s\n' "$out" | awk '$1 == "hostname" {print $2; exit}')
  user=$(printf '%s\n' "$out" | awk '$1 == "user" {print $2; exit}')
  port=$(printf '%s\n' "$out" | awk '$1 == "port" {print $2; exit}')
  [ -n "$host" ] && [ -n "$user" ] && [ -n "$port" ] || return 1
  safe_credential "$host" && safe_credential "$user" && safe_credential "$port" || return 1
  printf '%s\t%s\t%s\n' "$host" "$user" "$port"
}
collect_aliases() {
  target_identity=$(ssh_identity "$TARGET") || die "could not resolve SSH alias $TARGET"
  TARGET_ALIASES="$WORK/target-aliases"; : >"$TARGET_ALIASES"
  while IFS= read -r name; do
    if identity=$(ssh_identity "$name" 2>/dev/null) && [ "$identity" = "$target_identity" ]; then
      printf '%s\n' "$name" >>"$TARGET_ALIASES"
    fi
  done <"$ALIASES"
  grep -Fqx "$TARGET" "$TARGET_ALIASES" 2>/dev/null || printf '%s\n' "$TARGET" >>"$TARGET_ALIASES"
  TARGET_ALLOWED=
  while IFS= read -r name; do
    TARGET_ALLOWED=${TARGET_ALLOWED:+$TARGET_ALLOWED,}$name
  done <"$TARGET_ALIASES"
}
strip_path_block() {
  awk '/^# >>> remote-code-bridge PATH >>>$/ {x=1;next} /^# <<< remote-code-bridge PATH <<<$/{x=0;next} !x{print}' "$1" >"$2"
}
local_path() {
  case "$SHELL" in
    */zsh) rc="$HOME/.zshrc"; path_line='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac';;
    */bash) rc="$HOME/.bashrc"; path_line='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac';;
    */fish) rc="$HOME/.config/fish/config.fish"; path_line='contains -- $HOME/.local/bin $PATH; or set -gx PATH $HOME/.local/bin $PATH';;
    *) rc="$HOME/.profile"; path_line='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac';;
  esac
  mkdir -p "$(dirname "$rc")"; [ -f "$rc" ] || : >"$rc"; strip_path_block "$rc" "$WORK/rc"
  { cat "$WORK/rc"; printf '%s\n' '# >>> remote-code-bridge PATH >>>' "$path_line" '# <<< remote-code-bridge PATH <<<'; } >"$WORK/newrc"
  atomic "$WORK/newrc" "$rc" 600 follow
}
ssh_forward() {
  mdir="$HOME/.ssh/remote-code-bridge"; mcfg="$mdir/config"; msock="$mdir/sockets"
  [ ! -L "$mdir" ] || die "refusing symlinked managed directory $mdir"
  mkdir -p "$(dirname "$SSH_CONFIG")" "$mdir"; [ -f "$SSH_CONFIG" ] || : >"$SSH_CONFIG"
  [ ! -L "$msock" ] || die "refusing symlinked sockets directory $msock"
  mkdir -p "$msock"
  awk '/^# >>> remote-code-bridge include >>>$/ {x=1;next} /^# <<< remote-code-bridge include <<<$/{x=0;next} !x{print}' "$SSH_CONFIG" >"$WORK/ssh"
  { printf '%s\n' '# >>> remote-code-bridge include >>>' "Include $mcfg" '# <<< remote-code-bridge include <<<'; cat "$WORK/ssh"; } >"$WORK/newssh"
  atomic "$WORK/newssh" "$SSH_CONFIG" 600 follow
  {
    printf 'Host'
    while IFS= read -r alias; do printf ' %s' "$alias"; done <"$TARGET_ALIASES"
    printf '\n%s\n' '    RemoteForward 127.0.0.1:39731 127.0.0.1:39731' '    ExitOnForwardFailure yes' '    ServerAliveInterval 15' '    ServerAliveCountMax 3' '    ControlMaster auto' '    ControlPath ~/.ssh/remote-code-bridge/sockets/%C'
  } >"$WORK/managed"
  atomic "$WORK/managed" "$mcfg" 600
}
service() {
  case "$HOST_OS" in
    Linux)
      f="$HOME/.config/systemd/user/remote-code-bridge.service"
      { printf '%s\n' '[Unit]' 'Description=remote-code-bridge host daemon' '' '[Service]' 'Type=simple'; printf 'ExecStart=%s serve\n' "$HOST_BIN"; printf '%s\n' 'Restart=on-failure' '' '[Install]' 'WantedBy=default.target'; } >"$WORK/service"
      command -v systemctl >/dev/null 2>&1 || die 'systemctl is required on Linux'
      atomic "$WORK/service" "$f" 600; systemctl --user daemon-reload; systemctl --user enable --now remote-code-bridge.service; systemctl --user restart remote-code-bridge.service;;
    Darwin)
      f="$HOME/Library/LaunchAgents/com.remote-code-bridge.plist"
      { printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' '<plist version="1.0"><dict><key>Label</key><string>com.remote-code-bridge</string><key>ProgramArguments</key><array>'; printf '<string>%s</string><string>serve</string>\n' "$HOST_BIN"; printf '%s\n' '</array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/></dict></plist>'; } >"$WORK/plist"
      command -v launchctl >/dev/null 2>&1 || die 'launchctl is required on macOS'
      atomic "$WORK/plist" "$f" 600; launchctl bootout "gui/$(id -u)/com.remote-code-bridge" 2>/dev/null || :; launchctl bootstrap "gui/$(id -u)" "$f"; launchctl kickstart -k "gui/$(id -u)/com.remote-code-bridge";;
  esac
}
remote_path() {
  flavor=posix
  case "$1" in */zsh) r='$HOME/.zshrc';; */bash) r='$HOME/.bashrc';; */fish) r='$HOME/.config/fish/config.fish'; flavor=fish;; *) r='$HOME/.profile';; esac
  ssh "$TARGET" "sh -s -- \"$r\" $flavor" <<'REMOTE_PATH'
set -e
rc=$1; flavor=$2
hops=0
while [ -L "$rc" ]; do
  hops=$((hops + 1)); [ "$hops" -le 16 ] || { echo "too many symlinks resolving $1" >&2; exit 1; }
  link=$(readlink "$rc") || exit 1
  case "$link" in /*) rc=$link;; *) rc="$(dirname "$rc")/$link";; esac
done
mkdir -p "$(dirname "$rc")"; [ -f "$rc" ] || : >"$rc"
t=$(mktemp "$rc.remote-code-bridge.XXXXXX")
awk '/^# >>> remote-code-bridge PATH >>>$/ {x=1;next} /^# <<< remote-code-bridge PATH <<<$/{x=0;next} !x{print}' "$rc" >"$t"
printf '%s\n' '# >>> remote-code-bridge PATH >>>' >>"$t"
if [ "$flavor" = fish ]; then
  printf '%s\n' 'contains -- $HOME/.local/bin $PATH; or set -gx PATH $HOME/.local/bin $PATH' >>"$t"
else
  printf '%s\n' 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac' >>"$t"
fi
printf '%s\n' '# <<< remote-code-bridge PATH <<<' >>"$t"
chmod 600 "$t"; mv -f "$t" "$rc"
REMOTE_PATH
}
remote_install() {
  rshell=$(ssh "$TARGET" 'printf %s "$SHELL"' </dev/null 2>/dev/null || printf /bin/sh); remote_path "$rshell"
  ssh "$TARGET" '
set -e
umask 077
b="$HOME/.local/bin"; c="$b/code"; mkdir -p "$b"
t=$(mktemp "$b/.remote-code-bridge.XXXXXX")
trap "rm -f \"$t\"" EXIT HUP INT TERM
cat >"$t"; chmod 755 "$t"
if [ -e "$c" ] || [ -L "$c" ]; then
  if [ -L "$c" ]; then
    [ "$(readlink "$c" || :)" = remote-code-bridge ] || { echo "refusing unrelated $c" >&2; exit 73; }
  elif ! grep -Fxq "# Remote-side wrapper for remote-code-bridge." "$c"; then
    echo "refusing unrelated $c" >&2; exit 73
  fi
fi
mv -f "$t" "$b/remote-code-bridge"
rm -f "$c"; ln -s remote-code-bridge "$c"
' <"$1" &&
  ssh "$TARGET" 'set -e; umask 077; d="$HOME/.config/remote-code-bridge"; mkdir -p "$d"; t=$(mktemp "$d/.remote.env.XXXXXX"); trap "rm -f \"$t\"" EXIT HUP INT TERM; cat >"$t"; chmod 600 "$t"; mv -f "$t" "$d/remote.env"' <"$REMOTE_CONFIG"
}

[ "$#" -le 1 ] || die 'usage: install.sh [ssh-alias]'
TARGET=$1; [ -z "$TARGET" ] || valid_alias "$TARGET" || die 'SSH alias contains unsupported characters'
HOST_OS=$(uname -s); HOST_ARCH=$(arch "$(uname -m)"); HOST_TARGET=$(asset "$HOST_OS" "$HOST_ARCH" host)
SSH_CONFIG=$RCB_SSH_CONFIG; [ -n "$SSH_CONFIG" ] || SSH_CONFIG="$HOME/.ssh/config"
ALIASES="$WORK/aliases"; SEEN="$WORK/seen"; : >"$ALIASES"; : >"$SEEN"
read_config "$SSH_CONFIG" 0
if [ -n "$TARGET" ]; then probe "$TARGET" explicit || die "cannot reach Linux SSH alias $TARGET"
else
  [ -s "$ALIASES" ] || die "no concrete SSH Host aliases found in $SSH_CONFIG; pass one explicitly"
  while IFS= read -r n; do if probe "$n" discovery; then TARGET=$n; break; fi; done <"$ALIASES"
  [ -n "$TARGET" ] || die 'no configured SSH alias was reachable as Linux; pass an alias explicitly after fixing SSH access'
fi
collect_aliases
REMOTE_TARGET=$(asset "$REMOTE_OS" "$REMOTE_ARCH" remote)
HOST_BIN="$HOME/.local/bin/remote-code-bridge"; HOST_CONFIG="$HOME/.config/remote-code-bridge/host.env"; REMOTE_CONFIG="$WORK/remote.env"
host_release=$(fetch "$HOST_TARGET"); remote_release=$(fetch "$REMOTE_TARGET"); atomic "$host_release" "$HOST_BIN" 755
token=$(value REMOTE_CODE_BRIDGE_TOKEN "$HOST_CONFIG")
valid_token "$token" || token=$("$HOST_BIN" generate-token | tr -d '\r\n')
valid_token "$token" || die 'token generator returned an invalid token'
bind=$(value REMOTE_CODE_BRIDGE_BIND "$HOST_CONFIG"); [ -n "$bind" ] || bind=127.0.0.1
[ "$bind" = 127.0.0.1 ] || die 'REMOTE_CODE_BRIDGE_BIND must be 127.0.0.1'
code=$(value REMOTE_CODE_BRIDGE_CODE_BIN "$HOST_CONFIG"); [ -n "$code" ] || code=code
case "$code" in
  /*) CODE_BIN=$code;; *) CODE_BIN=$(command -v "$code" 2>/dev/null || :);;
esac
[ -n "$CODE_BIN" ] && [ -x "$CODE_BIN" ] || die 'could not find executable VS Code code command in PATH'
dry_run=$(value REMOTE_CODE_BRIDGE_DRY_RUN "$HOST_CONFIG"); [ -n "$dry_run" ] || dry_run=0
{ printf '%s\n' "REMOTE_CODE_BRIDGE_BIND=$bind" 'REMOTE_CODE_BRIDGE_PORT=39731'; printf 'REMOTE_CODE_BRIDGE_TOKEN=%s\nREMOTE_CODE_BRIDGE_CODE_BIN=%s\nREMOTE_CODE_BRIDGE_DEFAULT_HOST=%s\nREMOTE_CODE_BRIDGE_ALLOWED_HOSTS=%s\n' "$token" "$CODE_BIN" "$TARGET" "$TARGET_ALLOWED"; printf 'REMOTE_CODE_BRIDGE_DRY_RUN=%s\n' "$dry_run"; } >"$WORK/host.env"
atomic "$WORK/host.env" "$HOST_CONFIG" 600
{ printf '%s\n' 'REMOTE_CODE_BRIDGE_PORT=39731'; printf 'REMOTE_CODE_BRIDGE_HOST_ALIAS=%s\nREMOTE_CODE_BRIDGE_TOKEN=%s\n' "$TARGET" "$token"; } >"$REMOTE_CONFIG"; chmod 600 "$REMOTE_CONFIG"
local_path; ssh_forward; remote_install "$remote_release"; service
note "installed for SSH alias $TARGET; reconnect, then run code . on the remote"
