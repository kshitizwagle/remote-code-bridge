$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$Binary = Join-Path $Root 'target/debug/remote-code-bridge.exe'
if (-not (Test-Path -LiteralPath $Binary -PathType Leaf)) {
    throw "native Windows test binary is missing: $Binary"
}

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ('remote-code-bridge-native-' + [guid]::NewGuid().ToString('N'))
$Profile = Join-Path $TempRoot 'profile'
$Release = Join-Path $TempRoot 'release'
$FakeBin = Join-Path $TempRoot 'bin'
$Marker = Join-Path $TempRoot 'updated.marker'
$SshLog = Join-Path $TempRoot 'ssh.log'
$InstallScript = Join-Path $Root 'install.ps1'
$Port = 18081 + (Get-Random -Minimum 0 -Maximum 200)
$ServerJob = $null
$CurrentPrincipal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$OriginalUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$OriginalUserHome = [Environment]::GetEnvironmentVariable('HOME', 'User')
$SavedProcess = @{}
foreach ($name in @('USERPROFILE', 'HOME', 'Path', 'RCB_RELEASE_URL', 'RCB_UPDATE_MARKER', 'RCB_FAKE_SSH_LOG')) {
    $SavedProcess[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$TestOwnsTask = $false
$TestFailure = $null
$CleanupErrors = [Collections.Generic.List[string]]::new()
$UpdaterBaseline = [Collections.Generic.HashSet[int]]::new()
$UpdaterProcessIds = [Collections.Generic.HashSet[int]]::new()

function Fail([string]$Message) { throw "native Windows install/update: $Message" }

function Wait-Condition([scriptblock]$Condition, [string]$Message, [int]$Attempts = 120) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        try {
            if (& $Condition) { return }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    Fail $Message
}

function Get-EnvFileValue([string]$Path, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match "^$([regex]::Escape($Key))=(.*)$" } | Select-Object -First 1
    if ($null -eq $line) { return $null }
    return $line.Substring($Key.Length + 1)
}

function Start-ReleaseServer([string]$Directory, [int]$ListenPort) {
    return Start-Job -ScriptBlock {
        param($RootDirectory, $Port)
        $listener = [Net.HttpListener]::new()
        $listener.Prefixes.Add("http://127.0.0.1:$Port/") | Out-Null
        $listener.Start()
        try {
            while ($true) {
                $context = $listener.GetContext()
                try {
                    $relative = [Uri]::UnescapeDataString($context.Request.Url.AbsolutePath.TrimStart('/'))
                    if (-not $relative -or $relative.Contains('..')) {
                        $context.Response.StatusCode = 404
                    } else {
                        $path = Join-Path $RootDirectory ($relative -replace '/', '\')
                        if (Test-Path -LiteralPath $path -PathType Leaf) {
                            $bytes = [IO.File]::ReadAllBytes($path)
                            $context.Response.StatusCode = 200
                            $context.Response.ContentLength64 = $bytes.Length
                            $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
                        } else {
                            $context.Response.StatusCode = 404
                        }
                    }
                } finally {
                    $context.Response.Close()
                }
            }
        } finally {
            $listener.Stop()
            $listener.Close()
        }
    } -ArgumentList $Directory, $ListenPort
}

function Add-CleanupError([string]$Message) {
    $CleanupErrors.Add($Message)
}

function Get-DetachedUpdaterIds {
    foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue)) {
        if ($process.CommandLine -and $process.CommandLine.Contains('RCB_UPDATE_URL')) {
            [int]$process.ProcessId
        }
    }
}

function Remember-DetachedUpdaters {
    foreach ($processId in @(Get-DetachedUpdaterIds)) {
        if (-not $UpdaterBaseline.Contains($processId)) { [void]$UpdaterProcessIds.Add($processId) }
    }
}

function Stop-DetachedUpdater {
    foreach ($processId in $UpdaterProcessIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            try {
                Stop-Process -Id $processId -Force -ErrorAction Stop
            } catch {
                Add-CleanupError ("could not stop detached updater {0}: {1}" -f $processId, $_.Exception.Message)
            }
        }
    }
}

try {
    Import-Module ScheduledTasks -ErrorAction Stop
    if (Get-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction SilentlyContinue) {
        Fail 'scheduled task remote-code-bridge already exists'
    }
    $TestOwnsTask = $true

    New-Item -ItemType Directory -Force -Path $Profile, $Release, $FakeBin | Out-Null
    $sshConfig = Join-Path $Profile '.ssh/config'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sshConfig) | Out-Null
    Set-Content -LiteralPath $sshConfig -Encoding utf8 -Value @('Host devbox', '    HostName fake-remote', '    User rcbremote')
    $sshAcl = Get-Acl -LiteralPath $sshConfig
    $sshAcl.SetOwner([Security.Principal.NTAccount]$CurrentPrincipal)
    $sshAcl.SetAccessRuleProtection($true, $false)
    $sshAcl.SetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($CurrentPrincipal, 'FullControl', 'Allow'))
    Set-Acl -LiteralPath $sshConfig -AclObject $sshAcl

    Set-Content -LiteralPath (Join-Path $FakeBin 'code.cmd') -Encoding ascii -Value '@exit /b 0'
    $sshSource = @'
using System;
using System.IO;
using System.Text;

public static class FakeSsh
{
    public static int Main(string[] args)
    {
        string log = Environment.GetEnvironmentVariable("RCB_FAKE_SSH_LOG");
        string joined = string.Join(" ", args);
        if (!string.IsNullOrEmpty(log))
            File.AppendAllText(log, joined + Environment.NewLine);

        using (var input = Console.OpenStandardInput())
        using (var buffer = new MemoryStream())
        {
            input.CopyTo(buffer);
            if (!string.IsNullOrEmpty(log) && buffer.Length > 0)
            {
                string text = Encoding.UTF8.GetString(buffer.ToArray());
                string markerPrefix = "REMOTE_CODE_BRIDGE_" + "TOKEN=";
                int index = text.IndexOf(markerPrefix, StringComparison.Ordinal);
                if (joined.Contains("cat >"))
                    File.AppendAllText(log, "remote-file-input=" + buffer.Length + Environment.NewLine);
                if (index >= 0)
                    File.AppendAllText(log, "remote-config-token-present" + Environment.NewLine);
                if (text.IndexOf("native-test-remote-payload-v1", StringComparison.Ordinal) >= 0)
                    File.AppendAllText(log, "remote-payload-marker" + Environment.NewLine);
                if (!joined.Contains("cat >") && index < 0)
                    File.AppendAllText(log, "stdin-bytes=" + buffer.Length + Environment.NewLine);
            }
        }

        if (args.Length > 0 && args[0] == "-G")
        {
            Console.WriteLine("hostname fake-remote");
            Console.WriteLine("user rcbremote");
            Console.WriteLine("port 22");
        }
        else if (joined.Contains("uname -s"))
        {
            Console.WriteLine("Linux");
            Console.WriteLine("x86_64");
        }
        else if (joined.Contains("uname -m"))
        {
            Console.WriteLine("x86_64");
        }
        else if (joined.Contains("printf %s"))
        {
            Console.Write("/bin/bash");
        }
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $sshSource -OutputType ConsoleApplication -OutputAssembly (Join-Path $FakeBin 'ssh.exe')

    $hostAsset = Join-Path $Release 'remote-code-bridge-x86_64-pc-windows-msvc.exe'
    $remoteAsset = Join-Path $Release 'remote-code-bridge-x86_64-unknown-linux-musl'
    Copy-Item -LiteralPath $Binary -Destination $hostAsset
    Set-Content -LiteralPath $remoteAsset -Encoding ascii -Value 'native-test-remote-payload-v1'
    foreach ($asset in @($hostAsset, $remoteAsset)) {
        $hash = (Get-FileHash -LiteralPath $asset -Algorithm SHA256).Hash.ToLowerInvariant()
        Set-Content -LiteralPath "$asset.sha256" -Encoding ascii -Value "$hash  $(Split-Path -Leaf $asset)"
    }
    $servedInstaller = Join-Path $Release 'install.ps1'
    Copy-Item -LiteralPath $InstallScript -Destination $servedInstaller
    $markerLine = "`r`n[IO.File]::WriteAllText(`$env:RCB_UPDATE_MARKER, 'updated')`r`n"
    [IO.File]::AppendAllText($servedInstaller, $markerLine, [Text.Encoding]::UTF8)
    $ServerJob = Start-ReleaseServer $Release $Port

    $env:USERPROFILE = $Profile
    $env:HOME = $Profile
    $env:Path = "$FakeBin;$($SavedProcess['Path'])"
    $env:RCB_RELEASE_URL = "http://127.0.0.1:$Port"
    $env:RCB_UPDATE_MARKER = $Marker
    $env:RCB_FAKE_SSH_LOG = $SshLog
    [Environment]::SetEnvironmentVariable('HOME', $Profile, 'User')
    Wait-Condition {
        try { (Invoke-WebRequest -UseBasicParsing -Uri "$env:RCB_RELEASE_URL/install.ps1").StatusCode -eq 200 } catch { $false }
    } 'local release server did not start'

    & $InstallScript -SshAlias devbox | Out-Null
    $hostBin = Join-Path $Profile '.local/bin/remote-code-bridge.exe'
    $hostConfig = Join-Path $Profile '.config/remote-code-bridge/host.env'
    if (-not (Test-Path -LiteralPath $hostBin -PathType Leaf)) { Fail 'installer did not create the host binary' }
    if (-not (Test-Path -LiteralPath $hostConfig -PathType Leaf)) { Fail 'installer did not create host config' }
    $token = Get-EnvFileValue $hostConfig 'REMOTE_CODE_BRIDGE_TOKEN'
    if ($token -notmatch '^[0-9A-Fa-f]{64}$') { Fail 'installer did not create a valid host token' }
    if (-not (Select-String -LiteralPath $SshLog -Pattern 'uname -s; uname -m' -Quiet)) { Fail 'installer did not probe the SSH target' }
    if (-not (Select-String -LiteralPath $SshLog -Pattern 'remote-config-token-present' -Quiet)) { Fail 'installer did not transfer remote config' }
    if (-not (Select-String -LiteralPath $SshLog -Pattern 'remote-payload-marker' -Quiet)) { Fail 'installer did not transfer the remote payload' }
    $transferBefore = @(Select-String -LiteralPath $SshLog -Pattern '^remote-file-input=').Count
    if ($transferBefore -lt 2) { Fail 'installer did not transfer both remote payloads' }
    $configBefore = @(Select-String -LiteralPath $SshLog -Pattern '^remote-config-token-present$').Count
    $payloadBefore = @(Select-String -LiteralPath $SshLog -Pattern '^remote-payload-marker$').Count
    $task = Get-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction Stop
    if ($task.Principal.LogonType.ToString() -ne 'Interactive') { Fail 'scheduled task logon type is not Interactive' }
    if ($task.Principal.RunLevel.ToString() -ne 'Limited') { Fail 'scheduled task run level is not Limited' }
    Wait-Condition {
        try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:39731/healthz').StatusCode -eq 200 } catch { $false }
    } 'installed scheduled task did not start the host service'

    foreach ($processId in @(Get-DetachedUpdaterIds)) { [void]$UpdaterBaseline.Add($processId) }
    & $hostBin update devbox | Out-Null
    for ($attempt = 0; $attempt -lt 50 -and $UpdaterProcessIds.Count -eq 0; $attempt++) {
        Remember-DetachedUpdaters
        if ($UpdaterProcessIds.Count -eq 0) { Start-Sleep -Milliseconds 100 }
    }
    Wait-Condition { Test-Path -LiteralPath $Marker -PathType Leaf } 'Windows updater did not finish the local installer'
    if ((Get-Content -LiteralPath $Marker -Raw).Trim() -ne 'updated') { Fail 'Windows updater marker was incorrect' }
    $transferAfter = @(Select-String -LiteralPath $SshLog -Pattern '^remote-file-input=').Count
    if ($transferAfter -lt ($transferBefore + 2)) { Fail 'Windows updater did not re-transfer both remote payloads' }
    $configAfter = @(Select-String -LiteralPath $SshLog -Pattern '^remote-config-token-present$').Count
    if ($configAfter -lt ($configBefore + 1)) { Fail 'Windows updater did not re-transfer remote config' }
    $payloadAfter = @(Select-String -LiteralPath $SshLog -Pattern '^remote-payload-marker$').Count
    if ($payloadAfter -lt ($payloadBefore + 1)) { Fail 'Windows updater did not re-transfer remote binary' }
    $tokenAfter = Get-EnvFileValue $hostConfig 'REMOTE_CODE_BRIDGE_TOKEN'
    if ($tokenAfter -ne $token) { Fail 'Windows updater did not preserve the host token' }
    $task = Get-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction Stop
    if ($task.Principal.LogonType.ToString() -ne 'Interactive' -or $task.Principal.RunLevel.ToString() -ne 'Limited') { Fail 'updated scheduled task principal changed' }
    Wait-Condition {
        try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:39731/healthz').StatusCode -eq 200 } catch { $false }
    } 'updated scheduled task did not restart the host service'
    Write-Output 'native Windows install/update flow passed'
} catch {
    $TestFailure = $_.Exception
} finally {
    Stop-DetachedUpdater
    if ($TestOwnsTask) {
        try {
            $task = Get-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction SilentlyContinue
            if ($null -ne $task) { Stop-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction SilentlyContinue }
        } catch { Add-CleanupError ("could not stop test scheduled task: {0}" -f $_.Exception.Message) }
        if ($null -ne $task) {
            try {
                Unregister-ScheduledTask -TaskName 'remote-code-bridge' -Confirm:$false -ErrorAction Stop
            } catch { Add-CleanupError ("could not unregister test scheduled task: {0}" -f $_.Exception.Message) }
        }
        if (Get-ScheduledTask -TaskName 'remote-code-bridge' -ErrorAction SilentlyContinue) {
            Add-CleanupError 'test scheduled task still exists after cleanup'
        }
    }
    if ($null -ne $ServerJob) {
        try { Stop-Job -Job $ServerJob -ErrorAction Stop } catch { Add-CleanupError ("could not stop release server: {0}" -f $_.Exception.Message) }
        try { Remove-Job -Job $ServerJob -Force -ErrorAction Stop } catch { Add-CleanupError ("could not remove release server: {0}" -f $_.Exception.Message) }
    }
    try {
        [Environment]::SetEnvironmentVariable('Path', $OriginalUserPath, 'User')
        if ([Environment]::GetEnvironmentVariable('Path', 'User') -ne $OriginalUserPath) { Add-CleanupError 'user PATH was not restored' }
    } catch { Add-CleanupError ("could not restore user PATH: {0}" -f $_.Exception.Message) }
    try {
        [Environment]::SetEnvironmentVariable('HOME', $OriginalUserHome, 'User')
        if ([Environment]::GetEnvironmentVariable('HOME', 'User') -ne $OriginalUserHome) { Add-CleanupError 'user HOME was not restored' }
    } catch { Add-CleanupError ("could not restore user HOME: {0}" -f $_.Exception.Message) }
    foreach ($name in $SavedProcess.Keys) {
        try { [Environment]::SetEnvironmentVariable($name, $SavedProcess[$name], 'Process') }
        catch { Add-CleanupError ("could not restore process {0}: {1}" -f $name, $_.Exception.Message) }
    }
    try { Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction Stop }
    catch { Add-CleanupError ("could not remove temporary directory: {0}" -f $_.Exception.Message) }
}

if ($null -ne $TestFailure) {
    if ($CleanupErrors.Count -gt 0) { throw ("{0}; cleanup failed: {1}" -f $TestFailure.Message, ($CleanupErrors -join '; ')) }
    throw $TestFailure
}
if ($CleanupErrors.Count -gt 0) { throw ("cleanup failed: " + ($CleanupErrors -join '; ')) }
