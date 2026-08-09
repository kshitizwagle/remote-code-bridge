use crate::process_host_config;
use std::env;
#[cfg(not(windows))]
use std::io::Write;
use std::process::Command;
#[cfg(not(windows))]
use std::process::Stdio;

const RELEASE_BASE: &str =
    "https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download";

pub fn installer_url(release_base: Option<&str>, script: &str) -> String {
    let base = release_base
        .filter(|value| !value.is_empty())
        .unwrap_or(RELEASE_BASE)
        .trim_end_matches('/');
    format!("{base}/{script}")
}

pub fn is_valid_update_alias(alias: &str) -> bool {
    let mut bytes = alias.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

pub fn run_update(explicit_alias: Option<&str>) -> Result<(), String> {
    let alias = match explicit_alias {
        Some(alias) => alias.to_owned(),
        None => process_host_config()?
            .default_host
            .ok_or_else(|| "could not determine SSH alias; run the installer first".to_string())?,
    };
    if !is_valid_update_alias(&alias) {
        return Err("SSH alias contains unsupported characters".into());
    }

    let release_base = env::var("RCB_RELEASE_URL").ok();
    let url = if cfg!(windows) {
        installer_url(release_base.as_deref(), "install.ps1")
    } else {
        installer_url(release_base.as_deref(), "install.sh")
    };

    #[cfg(windows)]
    return run_windows(&alias, &url);
    #[cfg(not(windows))]
    run_posix(&alias, &url)
}

#[cfg(not(windows))]
fn run_posix(alias: &str, url: &str) -> Result<(), String> {
    let mut curl = Command::new("curl")
        .args([
            "-q",
            "-K",
            "-",
            "-fsSL",
            "--retry",
            "2",
            "--connect-timeout",
            "15",
            url,
        ])
        .env_remove("REMOTE_CODE_BRIDGE_TOKEN")
        .env_remove("token")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("could not start curl for update: {error}"))?;
    if let Some(token) = env::var("GH_TOKEN").ok().filter(|token| {
        token
            .bytes()
            .all(|byte| !byte.is_ascii_control() && byte != b'"' && byte != b'\\')
    }) {
        if let Some(stdin) = curl.stdin.as_mut() {
            writeln!(stdin, "header = \"Authorization: Bearer {token}\"")
                .map_err(|error| format!("could not prepare update download: {error}"))?;
        }
    }
    drop(curl.stdin.take());
    let output = curl
        .wait_with_output()
        .map_err(|error| format!("could not download update installer: {error}"))?;
    if !output.status.success() {
        return Err(
            "could not download update installer; if GitHub rate-limited, export GH_TOKEN and retry"
                .into(),
        );
    }

    let mut shell = Command::new("sh")
        .args(["-s", "--", alias])
        .env_remove("REMOTE_CODE_BRIDGE_TOKEN")
        .env_remove("token")
        .stdin(Stdio::piped())
        .spawn()
        .map_err(|error| format!("could not start update installer: {error}"))?;
    shell
        .stdin
        .take()
        .ok_or_else(|| "could not open update installer input".to_string())?
        .write_all(&output.stdout)
        .map_err(|error| format!("could not send update installer: {error}"))?;
    let status = shell
        .wait()
        .map_err(|error| format!("could not finish update installer: {error}"))?;
    if !status.success() {
        return Err("update installer failed".into());
    }
    println!("remote-code-bridge update: updated for SSH alias {alias}");
    Ok(())
}

#[cfg(windows)]
fn run_windows(alias: &str, url: &str) -> Result<(), String> {
    let script = r#"
$ErrorActionPreference = 'Stop'
Start-Sleep -Milliseconds 750
try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $env:RCB_UPDATE_URL -ErrorAction Stop
} catch {
    $status = 0
    try { $status = [int]$_.Exception.Response.StatusCode } catch { }
    if ($status -notin @(403, 429) -or -not $env:GH_TOKEN) { throw }
    $response = Invoke-WebRequest -UseBasicParsing -Uri $env:RCB_UPDATE_URL -Headers @{ Authorization = "Bearer $env:GH_TOKEN" } -ErrorAction Stop
}
& ([scriptblock]::Create($response.Content)) $env:RCB_UPDATE_ALIAS
"#;
    Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ])
        .env("RCB_UPDATE_URL", url)
        .env("RCB_UPDATE_ALIAS", alias)
        .env_remove("REMOTE_CODE_BRIDGE_TOKEN")
        .env_remove("token")
        .spawn()
        .map_err(|error| format!("could not start PowerShell updater: {error}"))?;
    println!("remote-code-bridge update: updater started; it will restart the host service");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{installer_url, is_valid_update_alias};

    #[test]
    fn update_url_uses_release_base_without_duplicate_slashes() {
        assert_eq!(
            installer_url(Some("https://example.test/releases/v1"), "install.sh"),
            "https://example.test/releases/v1/install.sh"
        );
        assert_eq!(
            installer_url(None, "install.ps1"),
            "https://github.com/kshitizwagle/remote-code-bridge/releases/latest/download/install.ps1"
        );
    }

    #[test]
    fn update_aliases_match_ssh_config_names() {
        assert!(is_valid_update_alias("devbox"));
        assert!(is_valid_update_alias("lab.example-2"));
        assert!(!is_valid_update_alias(""));
        assert!(!is_valid_update_alias("-oProxyCommand=evil"));
        assert!(!is_valid_update_alias("bad alias"));
    }
}
