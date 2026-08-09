# Windows Installer CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Windows ScheduledTasks logon-type failure and make real Windows PowerShell behavior a required GitHub Actions check.

**Architecture:** Extend the existing dependency-free PowerShell behavioral test by extracting and executing `Install-HostService`. Keep native ScheduledTasks object construction real, shadow only mutating task cmdlets, and run the same test under Windows PowerShell 5.1 and PowerShell Core 7 on `windows-2022`.

**Tech Stack:** PowerShell, Windows ScheduledTasks module, GitHub Actions

## Global Constraints

- Do not register, stop, or start a real scheduled task during tests.
- Do not add Pester or another dependency.
- Run the test under both `powershell` and `pwsh`.
- Preserve the existing Ubuntu test job unchanged.
- Guard ScheduledTasks behavior with `Win32NT` so the existing Linux PowerShell check still passes.

---

### Task 1: Cover and fix scheduled-task principal construction

**Files:**
- Modify: `scripts/windows-parser-test.ps1`
- Modify: `install.ps1:278`

**Interfaces:**
- Consumes: `Install-HostService([string]$HostBin)` from `install.ps1`
- Produces: a `ScheduledTaskPrincipal` with `LogonType=Interactive` and `RunLevel=Limited`

- [ ] **Step 1: Add the failing behavioral test**

On Windows only, extract `Install-HostService` from the parsed installer AST. Import `ScheduledTasks` before defining advanced-function test doubles for `Stop-ScheduledTask`, `Register-ScheduledTask`, and `Start-ScheduledTask`; leave `New-ScheduledTaskAction`, `New-ScheduledTaskTrigger`, and `New-ScheduledTaskPrincipal` native. Call the function and assert the captured principal. Use `[Environment]::OSVersion.Platform` rather than `$IsWindows`, which does not exist in Windows PowerShell 5.1 under strict mode.

```powershell
$serviceFunction = $ast.Find({
    $args[0] -is [Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Install-HostService'
}, $true)
if ($null -eq $serviceFunction) { throw 'Install-HostService not found' }
Invoke-Expression $serviceFunction.Extent.Text
$CurrentPrincipal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$script:RegisteredPrincipal = $null
Import-Module ScheduledTasks -ErrorAction Stop

function Stop-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
}
function Register-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, $Action, $Trigger, $Principal, [string]$Description, [switch]$Force)
    $script:RegisteredPrincipal = $Principal
}
function Start-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
}

Install-HostService 'C:\remote-code-bridge.exe'
if ($script:RegisteredPrincipal.LogonType -ne 'Interactive') { throw 'scheduled task principal is not interactive' }
if ($script:RegisteredPrincipal.RunLevel -ne 'Limited') { throw 'scheduled task principal is not limited' }
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/windows-parser-test.ps1
```

Expected: exit 1 because `InteractiveToken` cannot convert to `LogonTypeEnum`.

- [ ] **Step 3: Apply the minimal production fix**

In `Install-HostService`, replace only the enum argument:

```powershell
$principal = New-ScheduledTaskPrincipal -UserId $CurrentPrincipal -LogonType Interactive -RunLevel Limited
```

- [ ] **Step 4: Verify GREEN**

Run the same PowerShell command. Expected: `windows parser tests passed`, exit 0.

### Task 2: Require native Windows CI execution

**Files:**
- Modify: `.github/workflows/smoke.yml`
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `scripts/windows-parser-test.ps1`
- Produces: required `windows-installer` job for pushes and pull requests
- Produces: native Windows installer checks that gate the existing release build

- [ ] **Step 1: Add the Windows job**

```yaml
  windows-installer:
    runs-on: windows-2022
    steps:
      - uses: actions/checkout@v6
        with:
          persist-credentials: false
      - name: Test Windows installer with Windows PowerShell
        shell: powershell
        run: ./scripts/windows-parser-test.ps1
      - name: Test Windows installer with PowerShell Core
        shell: pwsh
        run: ./scripts/windows-parser-test.ps1
```

- [ ] **Step 2: Verify workflow and repository checks**

Replace the release workflow's Windows syntax-only check with the same two script invocations. Its existing Windows matrix leg already gates publication.

Run:

```sh
git diff --check
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo llvm-cov --all-targets --locked --fail-under-lines 80
./scripts/install-test.sh
./scripts/windows-install-test.sh
REMOTE_CODE_BRIDGE_PORT=49731 ./scripts/smoke-test.sh
```

Expected: every command exits 0; coverage remains at least 80%.

- [ ] **Step 3: Review and publish when requested**

Request code and security review for the final diff. After findings are resolved, stage the two workflow files, `install.ps1`, `scripts/windows-parser-test.ps1`, the design, and this plan, then use a conventional `fix:` commit.
