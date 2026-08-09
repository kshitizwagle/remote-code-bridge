#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FILE=$ROOT/install.ps1
[ -f "$FILE" ] || { printf '%s\n' 'missing install.ps1' >&2; exit 1; }
for needle in Invoke-WebRequest Get-FileHash ssh.exe RemoteForward ServerAliveInterval ServerAliveCountMax Register-ScheduledTask New-ScheduledTaskPrincipal; do
    grep -F -- "$needle" "$FILE" >/dev/null || { printf '%s\n' "install.ps1 lacks $needle" >&2; exit 1; }
done
if command -v pwsh >/dev/null 2>&1; then
    pwsh -NoLogo -NoProfile -NonInteractive -File "$ROOT/scripts/windows-parser-test.ps1"
fi
printf '%s\n' 'windows installer contract passed'
