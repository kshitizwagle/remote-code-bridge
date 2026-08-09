#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FILE=$ROOT/install.ps1
[ -f "$FILE" ] || { printf '%s\n' 'missing install.ps1' >&2; exit 1; }
for needle in Invoke-WebRequest Get-FileHash ssh.exe RemoteForward Register-ScheduledTask New-ScheduledTaskPrincipal; do
    grep -F -- "$needle" "$FILE" >/dev/null || { printf '%s\n' "install.ps1 lacks $needle" >&2; exit 1; }
done
printf '%s\n' 'windows installer contract passed'
