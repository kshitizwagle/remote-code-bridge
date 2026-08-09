use remote_code_bridge::{
    generate_token, parse_open_args, read_host_config, read_remote_config, resolve_remote_path,
    HostConfig, RemoteConfig,
};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

const TOKEN: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

static NEXT_TEMP_FILE: AtomicUsize = AtomicUsize::new(0);

struct TempConfig(PathBuf);

impl TempConfig {
    fn write(name: &str, contents: &str) -> Self {
        let suffix = NEXT_TEMP_FILE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "remote-code-bridge-{name}-{}-{suffix}.env",
            std::process::id()
        ));
        fs::write(&path, contents).unwrap();
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempConfig {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

#[test]
fn host_file_is_loaded_but_environment_takes_precedence() {
    let config = TempConfig::write(
        "host",
        &format!("REMOTE_CODE_BRIDGE_PORT=4000\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\nREMOTE_CODE_BRIDGE_DEFAULT_HOST=file-host\n"),
    );

    let loaded = read_host_config(config.path(), |key| match key {
        "REMOTE_CODE_BRIDGE_PORT" => Some("5000".into()),
        "REMOTE_CODE_BRIDGE_TOKEN" => Some(TOKEN.into()),
        _ => None,
    })
    .unwrap();

    assert_eq!(loaded.port, 5000);
    assert_eq!(loaded.token.as_deref(), Some(TOKEN));
    assert_eq!(loaded.default_host.as_deref(), Some("file-host"));
}

#[test]
fn remote_config_and_cli_arguments_preserve_only_safe_flags() {
    let config = TempConfig::write(
        "remote",
        &format!("REMOTE_CODE_BRIDGE_PORT=4010\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\n"),
    );

    let loaded = read_remote_config(config.path(), |_| None).unwrap();
    let request =
        parse_open_args(["--reuse-window", "-g", "src/main.rs"]).expect("supported command line");

    assert_eq!(
        loaded,
        RemoteConfig {
            port: 4010,
            host_alias: Some("devbox".into()),
            token: Some(TOKEN.into())
        }
    );
    assert_eq!(request.args, vec!["--reuse-window", "-g"]);
    assert!(request.path.ends_with("src/main.rs"));
}

#[test]
fn remote_cli_rejects_unsupported_flags_and_extra_paths() {
    assert_eq!(
        parse_open_args(["--install-extension", "rust"]).unwrap_err(),
        "unsupported code flag for remote bridge: --install-extension"
    );
    assert_eq!(
        parse_open_args(["one", "two"]).unwrap_err(),
        "only one path is supported"
    );
}

#[test]
fn relative_remote_paths_become_absolute_without_requiring_the_path_to_exist() {
    let current = std::env::current_dir().unwrap();
    assert_eq!(
        resolve_remote_path("not-created-yet").unwrap(),
        current.join("not-created-yet")
    );
}

#[test]
fn generated_tokens_are_hex_encoded_32_byte_values() {
    let first = generate_token().unwrap();
    let second = generate_token().unwrap();
    assert_eq!(first.len(), 64);
    assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
    assert_ne!(first, second);
}

#[test]
fn host_config_rejects_non_localhost_and_invalid_ports() {
    let config = TempConfig::write(
        "invalid-host",
        "REMOTE_CODE_BRIDGE_BIND=0.0.0.0\nREMOTE_CODE_BRIDGE_PORT=0\n",
    );

    let error = read_host_config(config.path(), |_| None).unwrap_err();
    assert!(error.contains("127.0.0.1"));
}

#[test]
fn remote_config_rejects_tokens_that_can_escape_an_http_header() {
    let config = TempConfig::write(
        "bad-token",
        "REMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\nREMOTE_CODE_BRIDGE_TOKEN=good\rInjected: header\n",
    );

    assert_eq!(
        read_remote_config(config.path(), |_| None).unwrap_err(),
        "REMOTE_CODE_BRIDGE_TOKEN must be exactly 64 ASCII hex characters"
    );
}

#[test]
fn host_and_remote_configs_require_a_64_character_hex_token() {
    let invalid = TempConfig::write(
        "invalid-token",
        "REMOTE_CODE_BRIDGE_TOKEN=not-a-token\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\n",
    );

    assert_eq!(
        read_host_config(invalid.path(), |_| None).unwrap_err(),
        "REMOTE_CODE_BRIDGE_TOKEN must be exactly 64 ASCII hex characters"
    );
    assert_eq!(
        read_remote_config(invalid.path(), |_| None).unwrap_err(),
        "REMOTE_CODE_BRIDGE_TOKEN must be exactly 64 ASCII hex characters"
    );
}

#[test]
fn host_default_alias_is_the_allowlist_when_none_is_configured() {
    let config = TempConfig::write("host-allowlist", "REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\n");

    let loaded = read_host_config(config.path(), |_| None).unwrap();

    assert_eq!(
        loaded
            .allowed_hosts
            .unwrap()
            .into_iter()
            .collect::<Vec<_>>(),
        ["devbox"]
    );
}

#[test]
fn debug_output_redacts_tokens() {
    let host = HostConfig {
        bind: "127.0.0.1".into(),
        port: 39731,
        token: Some("host-secret".into()),
        code_bin: "code".into(),
        default_host: Some("devbox".into()),
        allowed_hosts: None,
        dry_run: false,
    };
    let remote = RemoteConfig {
        port: 39731,
        host_alias: Some("devbox".into()),
        token: Some("remote-secret".into()),
    };

    assert!(!format!("{host:?}").contains("host-secret"));
    assert!(!format!("{remote:?}").contains("remote-secret"));
}
