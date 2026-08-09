# Windows Installer CI Design

## Goal

Catch Windows-only PowerShell installer failures before publishing a release, including invalid ScheduledTasks enum values.

## Design

Fix `Install-HostService` to pass the documented `Interactive` value to `New-ScheduledTaskPrincipal -LogonType`.

Extend `scripts/windows-parser-test.ps1` to execute the real `Install-HostService` function extracted from `install.ps1`. Keep the native, non-mutating ScheduledTasks object constructors (`New-ScheduledTaskAction`, `New-ScheduledTaskTrigger`, and `New-ScheduledTaskPrincipal`) real. Replace only `Stop-ScheduledTask`, `Register-ScheduledTask`, and `Start-ScheduledTask` with test functions so the test cannot alter the runner's task scheduler. Assert that registration receives a principal whose logon type is `Interactive` and run level is `Limited`.

Add a `windows-installer` job to `.github/workflows/smoke.yml` on `windows-2022`. Check out the repository with persisted credentials disabled, then run `scripts/windows-parser-test.ps1` once with Windows PowerShell (`shell: powershell`) and once with PowerShell Core (`shell: pwsh`). Either failure blocks pushes and pull requests.

Run the same two checks in the existing Windows build leg of `.github/workflows/release.yml`. The release build and publish chain already depends on that leg, so a Windows installer failure blocks publication without adding another job.

## Test Sequence

1. Add the service-function regression assertion and verify it fails on `InteractiveToken`.
2. Change only the enum value to `Interactive` and verify the PowerShell test passes locally.
3. Run the test under both Windows shells in GitHub Actions.
4. Run existing formatting, Clippy, Rust, coverage, installer, and smoke checks unchanged.

## Safety and Non-goals

The test creates ScheduledTasks objects in memory but never registers, stops, or starts a task. It does not run the full installer, access SSH, download releases, or modify Windows services. No new test framework or dependency is added.
