use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::thread;
use std::time::{Duration, Instant};

static NEXT_TEST_DIRECTORY: AtomicUsize = AtomicUsize::new(0);
const TOKEN: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new(name: &str) -> Self {
        let number = NEXT_TEST_DIRECTORY.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "remote-code-bridge-{name}-{}-{number}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    fn config(&self, name: &str, contents: &str) {
        let directory = self.0.join(".config/remote-code-bridge");
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join(name), contents).unwrap();
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct RunningServer(Child);

impl Drop for RunningServer {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn binary() -> String {
    if let Ok(path) = std::env::var("CARGO_BIN_EXE_remote-code-bridge") {
        return path;
    }
    if let Ok(path) = std::env::var("CARGO_BIN_EXE_remote_code_bridge") {
        return path;
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let executable = if cfg!(windows) {
        "remote-code-bridge.exe"
    } else {
        "remote-code-bridge"
    };
    let mut candidates = Vec::new();
    if let Some(target) = std::env::var_os("CARGO_TARGET_DIR") {
        candidates.push(PathBuf::from(target).join("debug").join(executable));
    }
    if std::env::var_os("LLVM_PROFILE_FILE").is_some() {
        candidates.push(
            manifest
                .join("target/llvm-cov-target/debug")
                .join(executable),
        );
    }
    candidates.push(manifest.join("target/debug").join(executable));
    if std::env::var_os("LLVM_PROFILE_FILE").is_none() {
        candidates.push(
            manifest
                .join("target/llvm-cov-target/debug")
                .join(executable),
        );
    }
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .expect("Cargo must build the remote-code-bridge binary")
        .display()
        .to_string()
}

fn unused_port() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.local_addr().unwrap().port()
}

fn start_server(home: &Path, port: u16) -> RunningServer {
    let mut child = Command::new(binary())
        .arg("serve")
        .env("HOME", home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return RunningServer(child);
        }
        thread::sleep(Duration::from_millis(25));
    }
    let _ = child.kill();
    let _ = child.wait();
    panic!("server did not become ready");
}

fn health(port: u16) -> String {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    stream
        .write_all(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    response
}

#[test]
fn serve_and_open_binary_round_trip_through_localhost() {
    let host = TestDirectory::new("host");
    let remote = TestDirectory::new("remote");
    let port = unused_port();
    host.config(
        "host.env",
        &format!(
            "REMOTE_CODE_BRIDGE_PORT={port}\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\nREMOTE_CODE_BRIDGE_CODE_BIN=/bin/true\nREMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\nREMOTE_CODE_BRIDGE_DRY_RUN=1\n"
        ),
    );
    remote.config(
        "remote.env",
        &format!(
            "REMOTE_CODE_BRIDGE_PORT={port}\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\n"
        ),
    );
    let _server = start_server(host.path(), port);

    let response = health(port);
    assert!(response.starts_with("HTTP/1.1 200 OK"));
    assert!(response.ends_with("{\"ok\":true,\"service\":\"remote-code-bridge\"}"));

    let opened = Command::new(binary())
        .args(["open", "--reuse-window", "."])
        .env("HOME", remote.path())
        .current_dir(remote.path())
        .output()
        .unwrap();
    assert!(
        opened.status.success(),
        "{}",
        String::from_utf8_lossy(&opened.stderr)
    );
    let expected = format!(
        "dry-run command: /bin/true --reuse-window --remote ssh-remote+devbox {}",
        remote.path().display()
    );
    assert_eq!(String::from_utf8(opened.stdout).unwrap().trim(), expected);
}

#[test]
fn slow_unauthenticated_connection_does_not_block_health() {
    let host = TestDirectory::new("slow-host");
    let port = unused_port();
    host.config(
        "host.env",
        &format!(
            "REMOTE_CODE_BRIDGE_PORT={port}\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\nREMOTE_CODE_BRIDGE_CODE_BIN=/bin/true\nREMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\nREMOTE_CODE_BRIDGE_DRY_RUN=1\n"
        ),
    );
    let _server = start_server(host.path(), port);
    let _slow = TcpStream::connect(("127.0.0.1", port)).unwrap();
    thread::sleep(Duration::from_millis(50));

    let started = Instant::now();
    let response = health(port);

    assert!(started.elapsed() < Duration::from_secs(1));
    assert!(response.starts_with("HTTP/1.1 200 OK"));
}

#[test]
fn binary_reports_configuration_and_cli_errors_without_starting_a_server() {
    let empty_home = TestDirectory::new("empty-home");
    let missing_config = Command::new(binary())
        .arg("open")
        .env("HOME", empty_home.path())
        .output()
        .unwrap();
    assert_eq!(missing_config.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&missing_config.stderr)
        .contains("REMOTE_CODE_BRIDGE_HOST_ALIAS is not set"));

    let unsupported = Command::new(binary())
        .args(["open", "--install-extension", "rust"])
        .env("HOME", empty_home.path())
        .output()
        .unwrap();
    assert_eq!(unsupported.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&unsupported.stderr)
        .contains("unsupported code flag for remote bridge"));

    let token = Command::new(binary())
        .arg("generate-token")
        .output()
        .unwrap();
    assert!(token.status.success());
    assert_eq!(String::from_utf8(token.stdout).unwrap().trim().len(), 64);

    let invalid_token_command = Command::new(binary())
        .args(["generate-token", "extra"])
        .output()
        .unwrap();
    assert_eq!(invalid_token_command.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&invalid_token_command.stderr)
        .contains("does not accept arguments"));

    let unknown_command = Command::new(binary()).arg("unknown").output().unwrap();
    assert_eq!(unknown_command.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&unknown_command.stderr).contains("usage:"));

    let update_without_install = Command::new(binary())
        .arg("update")
        .env("HOME", empty_home.path())
        .output()
        .unwrap();
    assert_eq!(update_without_install.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&update_without_install.stderr)
        .contains("could not determine SSH alias"));
}

#[cfg(unix)]
#[test]
fn update_reruns_the_release_installer_for_the_saved_alias() {
    use std::os::unix::fs::PermissionsExt;

    let home = TestDirectory::new("update-home");
    home.config(
        "host.env",
        "REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\nREMOTE_CODE_BRIDGE_TOKEN=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n",
    );
    let tools = TestDirectory::new("update-tools");
    fs::write(
        tools.path().join("curl"),
        "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '#!/bin/sh' 'printf \"alias=%s\\\\n\" \"$1\" > \"$HOME/update-args\"'\n",
    )
    .unwrap();
    fs::set_permissions(tools.path().join("curl"), fs::Permissions::from_mode(0o755)).unwrap();
    let path = format!(
        "{}:{}",
        tools.path().display(),
        std::env::var("PATH").unwrap_or_default()
    );

    let update = Command::new(binary())
        .arg("update")
        .env("HOME", home.path())
        .env("PATH", path)
        .output()
        .unwrap();
    assert!(
        update.status.success(),
        "{}",
        String::from_utf8_lossy(&update.stderr)
    );
    assert_eq!(
        fs::read_to_string(home.path().join("update-args")).unwrap(),
        "alias=devbox\n"
    );
}

#[cfg(unix)]
#[test]
fn update_rejects_a_truncated_installer_before_execution() {
    use std::os::unix::fs::PermissionsExt;

    let home = TestDirectory::new("truncated-update-home");
    home.config(
        "host.env",
        "REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\nREMOTE_CODE_BRIDGE_TOKEN=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n",
    );
    let tools = TestDirectory::new("truncated-update-tools");
    fs::write(
        tools.path().join("curl"),
        r#"#!/bin/sh
cat >/dev/null
printf '%s\n' '#!/bin/sh' 'printf ran > "$HOME/update-ran"'
i=0
while [ "$i" -lt 2048 ]; do printf '%s\n' '# padding'; i=$((i + 1)); done
printf "'\n"
"#,
    )
    .unwrap();
    fs::set_permissions(tools.path().join("curl"), fs::Permissions::from_mode(0o755)).unwrap();
    let path = format!(
        "{}:{}",
        tools.path().display(),
        std::env::var("PATH").unwrap_or_default()
    );

    let update = Command::new(binary())
        .arg("update")
        .env("HOME", home.path())
        .env("PATH", path)
        .output()
        .unwrap();

    assert!(!update.status.success());
    assert!(
        String::from_utf8_lossy(&update.stderr).contains("downloaded update installer is invalid")
    );
    assert!(!home.path().join("update-ran").exists());
}
