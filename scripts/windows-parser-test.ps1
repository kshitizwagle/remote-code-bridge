$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot '../install.ps1'),
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count) { throw $errors[0] }
$function = $ast.Find({
    $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Get-SshWords'
}, $true)
if ($null -eq $function) { throw 'Get-SshWords not found' }
Invoke-Expression $function.Extent.Text
function Fail([string]$Message) { throw $Message }

$words = @(Get-SshWords 'devbox')
if ($words.Count -ne 1 -or $words[0] -ne 'devbox') { throw 'Get-SshWords failed to parse an unquoted alias' }
$rejected = $false
try { Get-SshWords '"devbox' | Out-Null } catch { $rejected = $_.Exception.Message -eq 'unsupported SSH quoting' }
if (-not $rejected) { throw 'Get-SshWords accepted unmatched quoting' }

$identityFunction = $ast.Find({
    $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Get-SshIdentity'
}, $true)
if ($null -eq $identityFunction) { throw 'Get-SshIdentity not found' }
Invoke-Expression $identityFunction.Extent.Text
function Get-Text([byte[]]$Bytes) { return [Text.Encoding]::UTF8.GetString($Bytes) }
function Invoke-Native {
    param([string[]]$Arguments, [byte[]]$InputBytes)
    if (($Arguments -join ' ') -ne '-G devbox' -or $null -ne $InputBytes) { throw 'unexpected ssh invocation' }
    return [Text.Encoding]::UTF8.GetBytes("hostname example.test`nuser alice`nport 22`n")
}
$identity = Get-SshIdentity 'devbox'
if ($identity -ne "example.test`talice`t22") { throw 'Get-SshIdentity failed to read the effective SSH identity' }

Write-Output 'windows parser tests passed'
