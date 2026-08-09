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

Write-Output 'windows parser tests passed'
