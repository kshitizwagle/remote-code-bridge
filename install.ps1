# remote-code-bridge installer bootstrap for Windows hosts.
#
#   irm https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.ps1 | iex
#
# Finds Python 3.8+, downloads remote-code-bridge.pyz, checks its SHA-256, and runs its installer.
# Set $env:RCB_SSH_ALIAS first to choose the SSH alias explicitly.
param(
    [string]$SshAlias = $env:RCB_SSH_ALIAS
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$ReleaseUrl = if ($env:RCB_RELEASE_URL) { $env:RCB_RELEASE_URL } else { 'https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download' }
$Archive = 'remote-code-bridge.pyz'

function Fail([string]$Message) { throw "remote-code-bridge install: $Message" }

function Find-Python {
    # The `python.exe` in ...\WindowsApps is a Microsoft Store shortcut, not Python; skip it.
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        $command = Get-Command $candidate[0] -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command -or $command.Source -like '*\WindowsApps\*') { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        $found = & $command.Source @extra -c 'import sys; print(sys.executable) if sys.version_info >= (3, 8) else sys.exit(1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return ([string]$found).Trim() }
    }
    Fail "Python 3.8 or newer is required. Install it with: winget install Python.Python.3.12  (then open a new terminal)"
}

function Get-Release([string]$Name, [string]$Destination) {
    $url = "$ReleaseUrl/$Name"
    try {
        Invoke-WebRequest -Uri $url -OutFile $Destination -UseBasicParsing
    } catch {
        $status = 0
        try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        if ($status -notin @(403, 429) -or -not $GitHubToken) { Fail "download failed for $Name (HTTP $status). If GitHub rate-limited you, set `$env:GH_TOKEN and retry." }
        Invoke-WebRequest -Uri $url -OutFile $Destination -UseBasicParsing -Headers @{ Authorization = "Bearer $GitHubToken" }
    }
}

# Keep an optional GitHub token out of child processes; it is only used to retry a download.
$GitHubToken = $env:GH_TOKEN
$saved = @{}
foreach ($name in @('GH_TOKEN', 'GITHUB_TOKEN', 'REMOTE_CODE_BRIDGE_TOKEN')) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
$work = Join-Path ([IO.Path]::GetTempPath()) ('remote-code-bridge-install-' + [guid]::NewGuid().ToString('N'))
try {
    if ($env:PROCESSOR_ARCHITECTURE -notin @('AMD64', 'ARM64')) { Fail 'a 64-bit Windows is required' }
    $python = Find-Python
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    Write-Output "==> Downloading $Archive"
    $file = Join-Path $work $Archive
    Get-Release $Archive $file
    Get-Release "$Archive.sha256" "$file.sha256"
    $want = ((Get-Content -LiteralPath "$file.sha256" -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
    $got = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($want -ne $got) { Fail "checksum failed for $Archive" }

    $installArgs = @($file, 'install')
    if ($SshAlias) { $installArgs += $SshAlias }
    & $python @installArgs
    if ($LASTEXITCODE -ne 0) { Fail "installer exited with status $LASTEXITCODE" }
} finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
