param(
    [string]$SshAlias = $env:RCB_SSH_ALIAS
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Project = 'kshitizwagle/remote-code-bridge'
$ReleaseUrl = if ($env:RCB_RELEASE_URL) { $env:RCB_RELEASE_URL } else { "https://github.com/$Project/releases/latest/download" }
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("remote-code-bridge-install-" + [guid]::NewGuid().ToString('N'))
$SshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$CurrentPrincipal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$SavedEnvironment = @{}

function Fail([string]$Message) { throw "remote-code-bridge install: $Message" }

function Protect-Environment {
    foreach ($name in @('GH_TOKEN', 'GITHUB_TOKEN', 'RCB_GH_TOKEN', 'RCB_GH_TOKEN_HOLD', 'REMOTE_CODE_BRIDGE_TOKEN', 'token')) {
        $SavedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
}

function Restore-Environment {
    foreach ($name in $SavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $SavedEnvironment[$name], 'Process')
    }
}

function Quote-NativeArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Invoke-Native {
    param(
        [string[]]$Arguments,
        [byte[]]$InputBytes
    )
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $SshPath
    $psi.Arguments = (($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $psi
    if (-not $process.Start()) { Fail 'could not start ssh.exe' }
    if ($null -ne $InputBytes) {
        $process.StandardInput.BaseStream.Write($InputBytes, 0, $InputBytes.Length)
    }
    $process.StandardInput.Close()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    if ($process.ExitCode -ne 0) {
        Fail ("ssh failed for {0}: {1}" -f $Arguments[0], $stderr.Trim())
    }
    return [Text.Encoding]::UTF8.GetBytes($stdout)
}

function Invoke-Ssh([string]$Alias, [string]$Command, [byte[]]$InputBytes) {
    return Invoke-Native -Arguments @($Alias, $Command) -InputBytes $InputBytes
}

function Download([string]$Name) {
    $path = Join-Path $TempDir $Name
    $url = "$ReleaseUrl/$Name"
    try {
        Invoke-WebRequest -Uri $url -OutFile $path -UseBasicParsing -ErrorAction Stop
    } catch {
        $status = 0
        try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        if ($status -notin @(403, 429)) { Fail "download failed for $Name" }
        if (-not $GitHubToken) { Fail "download failed for $Name with HTTP $status. export GH_TOKEN and retry." }
        try {
            $headers = @{ Authorization = "Bearer $GitHubToken" }
            Invoke-WebRequest -Uri $url -Headers $headers -OutFile $path -UseBasicParsing -ErrorAction Stop
        } catch { Fail "download failed for $Name with HTTP $status. export GH_TOKEN and retry." }
    }
    return $path
}

function Download-Verified([string]$Name) {
    $file = Download $Name
    $sumFile = Download "$Name.sha256"
    $want = ((Get-Content -LiteralPath $sumFile -TotalCount 1) -split '\s+')[0].ToLowerInvariant()
    $got = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($want -ne $got) { Fail "checksum failed for $Name" }
    return $file
}

function Write-PrivateFile([string]$Path, [byte[]]$Bytes) {
    $directory = Split-Path -Parent $Path
    if (Test-Path -LiteralPath $Path) {
        $existing = Get-Item -LiteralPath $Path -Force
        if (($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail "refusing symlink destination $Path" }
    }
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllBytes($temporary, $Bytes)
    Move-Item -Force -LiteralPath $temporary -Destination $Path
    & icacls.exe $Path /inheritance:r /grant:r "${CurrentPrincipal}:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "could not protect $Path" }
}

function Find-CodeBin([string]$Configured) {
    if ($Configured) {
        if ([IO.Path]::IsPathRooted($Configured)) {
            if (Test-Path -LiteralPath $Configured -PathType Leaf) { return (Resolve-Path -LiteralPath $Configured).Path }
            return $null
        }
        $resolved = Get-Command -Name $Configured -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($resolved) { return $resolved.Source }
        return $null
    }
    foreach ($name in @('code.exe', 'code.cmd', 'code')) {
        $resolved = Get-Command -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($resolved) { return $resolved.Source }
    }
    return $null
}

function Get-Text([byte[]]$Bytes) { return [Text.Encoding]::UTF8.GetString($Bytes) }

function Get-EnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match "^$([regex]::Escape($Key))=(.*)$" } | Select-Object -First 1
    if ($line) { return $line.Substring($Key.Length + 1) }
    return $null
}

function Write-EnvFile([string]$Path, [hashtable]$Values) {
    $lines = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=') { $lines.Add($line); continue }
            if (-not $Values.ContainsKey($Matches[1])) { $lines.Add($line) }
        }
    }
    foreach ($key in $Values.Keys) { $lines.Add("$key=$($Values[$key])") }
    $utf8 = [Text.UTF8Encoding]::new($false)
    Write-PrivateFile $Path $utf8.GetBytes(($lines -join "`n") + "`n")
}

function Get-SshWords([string]$Text) {
    if (($Text.ToCharArray() | Where-Object { $_ -eq '"' }).Count % 2) { Fail 'unsupported SSH quoting' }
    $words = [Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($Text, '(?:"([^"]*)"|(\S+))')) {
        if ($match.Groups[1].Success) { $words.Add($match.Groups[1].Value) } else { $words.Add($match.Groups[2].Value) }
    }
    return $words
}

function Assert-SafeConfig([string]$Path) {
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail "refusing symlinked SSH config $Path" }
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.Owner -ne $CurrentPrincipal) { Fail "refusing unowned SSH config $Path" }
    $trusted = @($CurrentPrincipal, 'NT AUTHORITY\SYSTEM', 'BUILTIN\Administrators')
    foreach ($rule in $acl.Access) {
        if ($trusted -notcontains $rule.IdentityReference.Value -and $rule.FileSystemRights.ToString() -match 'Write|Modify|FullControl') {
            Fail "refusing writable SSH config $Path"
        }
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $clean = ($line -replace '\s+#.*$', '').Trim()
        if ($clean -match '^(?i:Match(?:\s+|=).*\bexec(?:\s|=|$)|ProxyCommand|KnownHostsCommand|LocalCommand|PKCS11Provider|SecurityKeyProvider)(?:\s|=|$)') {
            Fail "refusing executable SSH directive in $Path"
        }
    }
}

$SeenConfig = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$Aliases = [Collections.Generic.List[string]]::new()
function Read-SshConfig([string]$Path, [int]$Depth = 0) {
    if ($Depth -gt 16) { Fail 'SSH Include recursion exceeds 16 levels' }
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $full = (Resolve-Path -LiteralPath $Path).Path
    if (-not $SeenConfig.Add($full)) { return }
    Assert-SafeConfig $full
    $directory = Split-Path -Parent $full
    foreach ($raw in Get-Content -LiteralPath $full) {
        $line = ($raw -replace '\s+#.*$', '').Trim()
        if (-not $line) { continue }
        if ($line -match '^(?i:Host)(?:\s+|=)(.+)$') {
            foreach ($word in Get-SshWords $Matches[1]) {
                if ($word -match '^[A-Za-z0-9][A-Za-z0-9._-]*$' -and -not $Aliases.Contains($word)) { $Aliases.Add($word) }
            }
        } elseif ($line -match '^(?i:Include)(?:\s+|=)(.+)$') {
            foreach ($word in Get-SshWords $Matches[1]) {
                $pattern = if ($word -like '~/*') { Join-Path $HomeDir $word.Substring(2) } elseif ([IO.Path]::IsPathRooted($word)) { $word } else { Join-Path $directory $word }
                foreach ($include in Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue) { Read-SshConfig $include.FullName ($Depth + 1) }
            }
        }
    }
}

function Get-SshIdentity([string]$Alias) {
    $text = Get-Text (Invoke-Native -Arguments @('-G', $Alias) -InputBytes $null)
    $host = ([regex]::Match($text, '(?m)^hostname\s+(\S+)')).Groups[1].Value
    $user = ([regex]::Match($text, '(?m)^user\s+(\S+)')).Groups[1].Value
    $port = ([regex]::Match($text, '(?m)^port\s+(\S+)')).Groups[1].Value
    if (-not $host -or -not $user -or -not $port) { Fail "could not resolve SSH alias $Alias" }
    return "$host`t$user`t$port"
}

function Find-Target {
    if ($SshAlias -and $SshAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { Fail 'SSH alias contains unsupported characters' }
    $sshConfig = Join-Path $HomeDir '.ssh/config'
    if (Test-Path -LiteralPath $sshConfig) { Read-SshConfig $sshConfig }
    if ($SshAlias) {
        $candidates = @($SshAlias)
    } else {
        if ($Aliases.Count -eq 0) { Fail "no concrete SSH Host aliases found in $sshConfig; set RCB_SSH_ALIAS" }
        $candidates = $Aliases
    }
    foreach ($candidate in $candidates) {
        $args = if ($SshAlias) { @($candidate, 'uname -s; uname -m') } else { @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', $candidate, 'uname -s; uname -m') }
        try {
            $lines = (Get-Text (Invoke-Native -Arguments $args -InputBytes $null)).Trim() -split "`r?`n"
            if ($lines.Count -ge 2 -and $lines[0] -eq 'Linux') { return $candidate }
        } catch { }
    }
    Fail 'no configured SSH alias was reachable as Linux; fix SSH access or set RCB_SSH_ALIAS'
}

function Install-PathBlock([string]$RemoteShell) {
    $flavor = if ($RemoteShell -match '/fish$') { 'fish' } elseif ($RemoteShell -match '/(zsh|bash)$') { $Matches[1] } else { 'posix' }
    $rc = if ($flavor -eq 'fish') { '$HOME/.config/fish/config.fish' } elseif ($flavor -eq 'zsh') { '$HOME/.zshrc' } elseif ($flavor -eq 'bash') { '$HOME/.bashrc' } else { '$HOME/.profile' }
    $pathScript = @'
set -e
rc=$1; flavor=$2
mkdir -p "$(dirname "$rc")"; [ -f "$rc" ] || : >"$rc"
t=$(mktemp "$rc.remote-code-bridge.XXXXXX")
awk '/^# >>> remote-code-bridge PATH >>>$/ {x=1;next} /^# <<< remote-code-bridge PATH <<</ {x=0;next} !x{print}' "$rc" >"$t"
printf '%s\n' '# >>> remote-code-bridge PATH >>>' >>"$t"
if [ "$flavor" = fish ]; then printf '%s\n' 'contains -- $HOME/.local/bin $PATH; or set -gx PATH $HOME/.local/bin $PATH' >>"$t"; else printf '%s\n' 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac' >>"$t"; fi
printf '%s\n' '# <<< remote-code-bridge PATH <<<' >>"$t"
chmod 600 "$t"; mv -f "$t" "$rc"
'@
    $bytes = [Text.Encoding]::UTF8.GetBytes($pathScript)
    Invoke-Ssh $Target "sh -s -- `"$rc`" $flavor" $bytes | Out-Null
}

function Install-Remote([string]$Alias, [byte[]]$Binary, [byte[]]$Config) {
    $random = [guid]::NewGuid().ToString('N')
    $remoteTmp = '$HOME/.local/bin/.remote-code-bridge.' + $random
    Invoke-Ssh $Alias "umask 077; mkdir -p `$HOME/.local/bin; cat > `"$remoteTmp`"" $Binary | Out-Null
    $finish = @'
set -e
b="$HOME/.local/bin"; c="$b/code"; t="__REMOTE_TMP__"
chmod 755 "$t"
if [ -e "$c" ] || [ -L "$c" ]; then
  if [ -L "$c" ]; then [ "$(readlink "$c" || :)" = remote-code-bridge ] || exit 73
  else grep -Fxq '# Remote-side wrapper for remote-code-bridge.' "$c" || exit 73; fi
fi
mv -f "$t" "$b/remote-code-bridge"; rm -f "$c"; ln -s remote-code-bridge "$c"
'@.Replace('__REMOTE_TMP__', $remoteTmp)
    Invoke-Ssh $Alias 'sh -s' ([Text.Encoding]::UTF8.GetBytes($finish)) | Out-Null
    $configTmp = '$HOME/.config/remote-code-bridge/.remote.env.' + $random
    Invoke-Ssh $Alias "umask 077; mkdir -p `$HOME/.config/remote-code-bridge; cat > `"$configTmp`"" $Config | Out-Null
    $configFinish = @'
set -e
d="$HOME/.config/remote-code-bridge"; t="__REMOTE_CONFIG__"
chmod 600 "$t"; mv -f "$t" "$d/remote.env"
'@.Replace('__REMOTE_CONFIG__', $configTmp)
    Invoke-Ssh $Alias 'sh -s' ([Text.Encoding]::UTF8.GetBytes($configFinish)) | Out-Null
}

function Install-HostService([string]$HostBin) {
    Stop-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $HostBin -Argument 'serve'
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $CurrentPrincipal -LogonType InteractiveToken -RunLevel Limited
    Register-ScheduledTask -TaskName 'remote-code-bridge' -Action $action -Trigger $trigger -Principal $principal -Description 'remote-code-bridge host daemon' -Force | Out-Null
    Start-ScheduledTask -TaskName 'remote-code-bridge'
}

try {
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    Protect-Environment
    $GitHubToken = $SavedEnvironment['GH_TOKEN']
    $hostArch = 'x86_64'
    $target = Find-Target
    $targetIdentity = Get-SshIdentity $target
    $targetAliases = [Collections.Generic.List[string]]::new()
    foreach ($alias in $Aliases) { if ((Get-SshIdentity $alias) -eq $targetIdentity) { $targetAliases.Add($alias) } }
    if (-not $targetAliases.Contains($target)) { $targetAliases.Add($target) }
    $allowed = $targetAliases -join ','

    $hostAsset = "remote-code-bridge-$hostArch-pc-windows-msvc.exe"
    $remoteOsArch = (Get-Text (Invoke-Ssh $target 'uname -m' $null)).Trim()
    $remoteArch = if ($remoteOsArch -match 'aarch64|arm64') { 'aarch64' } else { 'x86_64' }
    $remoteAsset = "remote-code-bridge-$remoteArch-unknown-linux-musl"
    $hostSource = Download-Verified $hostAsset
    $remoteSource = Download-Verified $remoteAsset
    $hostBin = Join-Path $HomeDir '.local/bin/remote-code-bridge.exe'
    Write-PrivateFile $hostBin ([IO.File]::ReadAllBytes($hostSource))
    $hostConfig = Join-Path $HomeDir '.config/remote-code-bridge/host.env'
    $token = Get-EnvValue $hostConfig 'REMOTE_CODE_BRIDGE_TOKEN'
    if (-not $token -or $token -notmatch '^[0-9A-Fa-f]{64}$') { $token = (& $hostBin generate-token).Trim() }
    if ($token -notmatch '^[0-9A-Fa-f]{64}$') { Fail 'token generator returned an invalid token' }
    $bind = Get-EnvValue $hostConfig 'REMOTE_CODE_BRIDGE_BIND'; if (-not $bind) { $bind = '127.0.0.1' }
    if ($bind -ne '127.0.0.1') { Fail 'REMOTE_CODE_BRIDGE_BIND must be 127.0.0.1' }
    $codeBin = Find-CodeBin (Get-EnvValue $hostConfig 'REMOTE_CODE_BRIDGE_CODE_BIN')
    if (-not $codeBin) { Fail 'could not find executable VS Code code command in PATH' }
    $dryRun = Get-EnvValue $hostConfig 'REMOTE_CODE_BRIDGE_DRY_RUN'; if (-not $dryRun) { $dryRun = '0' }
    Write-EnvFile $hostConfig @{
        REMOTE_CODE_BRIDGE_BIND = $bind
        REMOTE_CODE_BRIDGE_PORT = '39731'
        REMOTE_CODE_BRIDGE_TOKEN = $token
        REMOTE_CODE_BRIDGE_CODE_BIN = $codeBin
        REMOTE_CODE_BRIDGE_DEFAULT_HOST = $target
        REMOTE_CODE_BRIDGE_ALLOWED_HOSTS = $allowed
        REMOTE_CODE_BRIDGE_DRY_RUN = $dryRun
    }
    $remoteConfig = [Text.Encoding]::UTF8.GetBytes("REMOTE_CODE_BRIDGE_PORT=39731`nREMOTE_CODE_BRIDGE_HOST_ALIAS=$target`nREMOTE_CODE_BRIDGE_TOKEN=$token`n")

    $sshDir = Join-Path $HomeDir '.ssh'; $managedDir = Join-Path $sshDir 'remote-code-bridge'; $managedConfig = Join-Path $managedDir 'config'; $sshConfig = Join-Path $sshDir 'config'
    $managedInclude = 'Include "' + ($managedConfig -replace '\\', '/') + '"'
    $oldSsh = if (Test-Path -LiteralPath $sshConfig) { Get-Content -LiteralPath $sshConfig } else { @() }
    $filtered = [Collections.Generic.List[string]]::new(); $inside = $false
    foreach ($line in $oldSsh) { if ($line -eq '# >>> remote-code-bridge include >>>') { $inside = $true; continue }; if ($line -eq '# <<< remote-code-bridge include <<<') { $inside = $false; continue }; if (-not $inside) { $filtered.Add($line) } }
    $sshText = @('# >>> remote-code-bridge include >>>', $managedInclude, '# <<< remote-code-bridge include <<<') + $filtered
    Write-PrivateFile $sshConfig ([Text.Encoding]::UTF8.GetBytes(($sshText -join "`n") + "`n"))
    $managedText = "Host $($targetAliases -join ' ')`n    RemoteForward 127.0.0.1:39731 127.0.0.1:39731`n    ExitOnForwardFailure yes`n"
    Write-PrivateFile $managedConfig ([Text.Encoding]::UTF8.GetBytes($managedText))

    $remoteShell = (Get-Text (Invoke-Ssh $target 'printf %s "$SHELL"' $null)).Trim()
    Install-PathBlock $remoteShell
    Install-Remote $target ([IO.File]::ReadAllBytes($remoteSource)) $remoteConfig
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (($userPath -split ';') -contains (Join-Path $HomeDir '.local/bin'))) { [Environment]::SetEnvironmentVariable('Path', ((Join-Path $HomeDir '.local/bin') + ';' + $userPath), 'User') }
    $env:Path = (Join-Path $HomeDir '.local/bin') + ';' + $env:Path
    Install-HostService $hostBin
    Write-Output "remote-code-bridge install: installed for SSH aliases $allowed; reconnect, then run code . on the remote"
} catch {
    Write-Error $_.Exception.Message
    throw
} finally {
    Restore-Environment
    Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue
}
