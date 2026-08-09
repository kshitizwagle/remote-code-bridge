use crate::config::HostConfig;
use crate::{APP_NAME, MAX_REQUEST_BYTES};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use subtle::ConstantTimeEq;

const MAX_CHILDREN: usize = 8;
const CHILD_LIMIT_ERROR: &str = "too many VS Code requests in flight";
static CHILD_COUNT: AtomicUsize = AtomicUsize::new(0);

#[derive(Clone, PartialEq, Eq)]
pub struct HttpRequest {
    pub method: String,
    pub path: String,
    pub headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

impl Response {
    pub fn error(status: u16, error: &str) -> Self {
        Self::from_json(status, serde_json::json!({"ok": false, "error": error}))
    }

    pub fn from_json(status: u16, payload: serde_json::Value) -> Self {
        Self {
            status,
            body: serde_json::to_vec(&payload).expect("JSON value serializes"),
        }
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::from_slice(&self.body).expect("bridge response is JSON")
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct OpenRequest {
    pub host: Option<String>,
    pub path: String,
    #[serde(default)]
    pub args: Vec<String>,
}

pub fn handle_request(config: &HostConfig, request: HttpRequest) -> Response {
    if request.method == "GET" && request.path == "/healthz" && request.body.is_empty() {
        return Response::from_json(200, serde_json::json!({"ok": true, "service": APP_NAME}));
    }
    if request.method == "GET" && request.path == "/healthz" {
        return Response::error(400, "invalid request size");
    }
    if request.method != "POST" || request.path != "/open" {
        return Response::error(404, "not found");
    }
    if request.body.is_empty() || request.body.len() > MAX_REQUEST_BYTES {
        return Response::error(400, "invalid request size");
    }

    let Some(token) = config.token.as_deref() else {
        return Response::error(500, "REMOTE_CODE_BRIDGE_TOKEN is not set on host");
    };
    let authorization = request
        .headers
        .get("authorization")
        .map(String::as_str)
        .unwrap_or("");
    let expected = format!("Bearer {token}");
    if authorization
        .as_bytes()
        .ct_eq(expected.as_bytes())
        .unwrap_u8()
        != 1
    {
        return Response::error(401, "unauthorized");
    }

    let open: OpenRequest = match serde_json::from_slice(&request.body) {
        Ok(open) => open,
        Err(_) => return Response::error(400, "invalid json"),
    };
    let host = open.host.as_deref().or(config.default_host.as_deref());
    if !Path::new(&open.path).is_absolute() {
        return Response::error(400, "path must be an absolute remote path");
    }
    if open.path.chars().any(char::is_control) {
        return Response::error(400, "invalid remote path");
    }
    let Some(host) = host.filter(|host| !host.is_empty()) else {
        return Response::error(400, "host alias is required");
    };
    if !is_concrete_host_alias(host) {
        return Response::error(400, "invalid host alias");
    }
    if config
        .allowed_hosts
        .as_ref()
        .is_some_and(|hosts| !hosts.contains(host))
    {
        return Response::error(403, &format!("host alias not allowed: {host}"));
    }

    let command = match build_code_command(config, &open) {
        Ok(command) => command,
        Err(error) => return Response::error(400, &error),
    };
    if config.dry_run {
        return Response::from_json(
            200,
            serde_json::json!({"ok": true, "dry_run": true, "command": command}),
        );
    }
    if !code_binary_exists(&config.code_bin) {
        return Response::error(500, &format!("'{}' not found in PATH", config.code_bin));
    }
    if let Err(error) = spawn_and_reap(&command) {
        let status = if error == CHILD_LIMIT_ERROR { 503 } else { 500 };
        return Response::error(status, &format!("failed to launch VS Code: {error}"));
    }
    Response::from_json(200, serde_json::json!({"ok": true, "command": command}))
}

fn is_concrete_host_alias(host: &str) -> bool {
    let mut characters = host.bytes();
    characters
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && characters.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn spawn_and_reap(command: &[String]) -> Result<Receiver<Result<(), String>>, String> {
    let permit = try_acquire_child_slot().ok_or_else(|| CHILD_LIMIT_ERROR.to_string())?;
    let mut child = Command::new(&command[0])
        .args(&command[1..])
        .env_remove("REMOTE_CODE_BRIDGE_TOKEN")
        .env_remove("GH_TOKEN")
        .env_remove("GITHUB_TOKEN")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| error.to_string())?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let result = child.wait().map(|_| ()).map_err(|error| error.to_string());
        drop(permit);
        let _ = sender.send(result);
    });
    Ok(receiver)
}

struct ChildPermit;

impl Drop for ChildPermit {
    fn drop(&mut self) {
        CHILD_COUNT.fetch_sub(1, Ordering::Release);
    }
}

fn try_acquire_child_slot() -> Option<ChildPermit> {
    let mut current = CHILD_COUNT.load(Ordering::Acquire);
    loop {
        if current >= MAX_CHILDREN {
            return None;
        }
        match CHILD_COUNT.compare_exchange_weak(
            current,
            current + 1,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return Some(ChildPermit),
            Err(next) => current = next,
        }
    }
}

pub fn build_code_command(config: &HostConfig, open: &OpenRequest) -> Result<Vec<String>, String> {
    let host = open
        .host
        .as_deref()
        .or(config.default_host.as_deref())
        .ok_or_else(|| "host alias is required".to_string())?;
    let safe_flags = ["--reuse-window", "-r", "--new-window", "-n", "--goto", "-g"];
    let goto_flag = open
        .args
        .iter()
        .find(|arg| matches!(arg.as_str(), "--goto" | "-g"));
    let forwarded = open
        .args
        .iter()
        .filter(|arg| {
            safe_flags.contains(&arg.as_str()) && !matches!(arg.as_str(), "--goto" | "-g")
        })
        .cloned();
    Ok(std::iter::once(config.code_bin.clone())
        .chain(forwarded)
        .chain(["--remote".to_string(), format!("ssh-remote+{host}")])
        .chain(goto_flag.cloned())
        .chain([open.path.clone()])
        .collect())
}

fn code_binary_exists(binary: &str) -> bool {
    let path = Path::new(binary);
    if path.components().count() > 1 {
        return path.is_file();
    }
    std::env::var_os("PATH").is_some_and(|paths| {
        std::env::split_paths(&paths).any(|directory| directory.join(binary).is_file())
    })
}

#[cfg(test)]
mod tests {
    use super::{spawn_and_reap, try_acquire_child_slot, MAX_CHILDREN};
    use std::sync::Mutex;
    use std::time::Duration;

    static CHILD_TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn short_lived_child_is_reaped() {
        let _guard = CHILD_TEST_LOCK.lock().unwrap();
        let result = spawn_and_reap(&["/bin/true".to_owned()])
            .unwrap()
            .recv_timeout(Duration::from_secs(1))
            .unwrap();
        assert_eq!(result, Ok(()));
    }

    #[test]
    fn child_slots_are_bounded() {
        let _guard = CHILD_TEST_LOCK.lock().unwrap();
        let permits: Vec<_> = (0..MAX_CHILDREN)
            .map(|_| try_acquire_child_slot().expect("child slot"))
            .collect();
        assert!(try_acquire_child_slot().is_none());
        drop(permits);
    }
}
