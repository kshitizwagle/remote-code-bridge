# Native Installer and Update CI Design

## Goal

Exercise the complete release installer and `remote-code-bridge update` flow on native GitHub-hosted Linux and Windows runners using release artifacts served only from loopback.

## Design

Add two end-to-end harnesses and invoke them from the existing smoke and release workflows:

- Linux runs the real `install.sh` and installed binary updater against a local HTTP release server. Docker Compose provides two Linux containers, one acting as the installer host and one acting as the SSH remote. The test uses real `ssh`, `curl`, checksum verification, shell execution, binary installation, and updater execution. The service-manager boundary remains an isolated test double so the runner does not depend on a user systemd session.
- Windows runs the real `install.ps1` and installed Windows binary updater against a local HTTP release server. A small test-only native `ssh.exe` fixture emulates the unavoidable Linux SSH target; the Windows Scheduled Task cmdlets remain real. The test verifies task registration, update completion, token/config preservation, and cleans up the temporary task and user PATH mutation.

The local release server serves the binaries, checksum files, and installer scripts built or staged from the current checkout. No test downloads a published release or sends credentials to an external host.

## Workflow

The Ubuntu smoke job builds the current binary, starts the Compose SSH fixture, and runs the Linux harness. The Windows installer job builds the Windows binary and runs the PowerShell harness in both Windows PowerShell and PowerShell Core. The release workflow runs the corresponding native harness in its existing Ubuntu test job and Windows build leg before publishing artifacts.

## Safety and cleanup

- Bind the release server and SSH fixture to loopback only.
- Generate all tokens and keys inside temporary directories; never use repository or real user configuration.
- Fail if the temporary Windows scheduled-task name already exists.
- Always stop/remove the Windows task, restore the original user PATH, stop containers and the release server, and remove temporary files.
- Keep the existing parser and contract tests; the new harnesses add execution coverage rather than replacing them.

## Success criteria

Each native job must prove that installation creates the expected host/remote configuration, that `update` downloads and executes the current installer from the local server, that the token survives the update, and that the platform service boundary is invoked successfully. A failed installer, checksum, SSH transfer, updater, or cleanup assertion fails the job.
