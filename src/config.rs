use crate::DEFAULT_PORT;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone, PartialEq, Eq)]
pub struct HostConfig {
    pub bind: String,
    pub port: u16,
    pub token: Option<String>,
    pub code_bin: String,
    pub default_host: Option<String>,
    pub allowed_hosts: Option<BTreeSet<String>>,
    pub dry_run: bool,
}

#[derive(Clone, PartialEq, Eq)]
pub struct RemoteConfig {
    pub port: u16,
    pub host_alias: Option<String>,
    pub token: Option<String>,
}

impl fmt::Debug for HostConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("HostConfig")
            .field("bind", &self.bind)
            .field("port", &self.port)
            .field("token", &self.token.as_ref().map(|_| "<redacted>"))
            .field("code_bin", &self.code_bin)
            .field("default_host", &self.default_host)
            .field("allowed_hosts", &self.allowed_hosts)
            .field("dry_run", &self.dry_run)
            .finish()
    }
}

impl fmt::Debug for RemoteConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RemoteConfig")
            .field("port", &self.port)
            .field("host_alias", &self.host_alias)
            .field("token", &self.token.as_ref().map(|_| "<redacted>"))
            .finish()
    }
}

pub fn default_config_path(name: &str) -> Result<PathBuf, String> {
    let home = env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .ok_or_else(|| "HOME/USERPROFILE is not set".to_string())?;
    Ok(PathBuf::from(home)
        .join(".config/remote-code-bridge")
        .join(name))
}

pub fn read_host_config<F>(path: &Path, environment: F) -> Result<HostConfig, String>
where
    F: Fn(&str) -> Option<String>,
{
    let values = read_values(path)?;
    let bind = value(&values, &environment, "REMOTE_CODE_BRIDGE_BIND")
        .unwrap_or_else(|| "127.0.0.1".into());
    if bind != "127.0.0.1" {
        return Err("REMOTE_CODE_BRIDGE_BIND must be 127.0.0.1".into());
    }

    let port = parse_port(value(&values, &environment, "REMOTE_CODE_BRIDGE_PORT"))?;
    let default_host = value(&values, &environment, "REMOTE_CODE_BRIDGE_DEFAULT_HOST");
    let allowed_hosts = value(&values, &environment, "REMOTE_CODE_BRIDGE_ALLOWED_HOSTS")
        .and_then(|raw| {
            let hosts: BTreeSet<_> = raw
                .split(',')
                .map(str::trim)
                .filter(|host| !host.is_empty())
                .map(ToOwned::to_owned)
                .collect();
            (!hosts.is_empty()).then_some(hosts)
        })
        .or_else(|| {
            default_host
                .as_ref()
                .map(|host| [host.clone()].into_iter().collect())
        });

    Ok(HostConfig {
        bind,
        port,
        token: token(&values, &environment)?,
        code_bin: value(&values, &environment, "REMOTE_CODE_BRIDGE_CODE_BIN")
            .unwrap_or_else(|| "code".into()),
        default_host,
        allowed_hosts,
        dry_run: value(&values, &environment, "REMOTE_CODE_BRIDGE_DRY_RUN")
            .is_some_and(|raw| matches!(raw.as_str(), "1" | "true" | "yes")),
    })
}

pub fn read_remote_config<F>(path: &Path, environment: F) -> Result<RemoteConfig, String>
where
    F: Fn(&str) -> Option<String>,
{
    let values = read_values(path)?;
    Ok(RemoteConfig {
        port: parse_port(value(&values, &environment, "REMOTE_CODE_BRIDGE_PORT"))?,
        host_alias: value(&values, &environment, "REMOTE_CODE_BRIDGE_HOST_ALIAS"),
        token: token(&values, &environment)?,
    })
}

pub fn process_host_config() -> Result<HostConfig, String> {
    read_host_config(&default_config_path("host.env")?, process_value)
}

pub fn process_remote_config() -> Result<RemoteConfig, String> {
    read_remote_config(&default_config_path("remote.env")?, process_value)
}

fn process_value(key: &str) -> Option<String> {
    env::var(key).ok().filter(|value| !value.is_empty())
}

fn parse_port(raw: Option<String>) -> Result<u16, String> {
    let raw = raw.unwrap_or_else(|| DEFAULT_PORT.to_string());
    raw.parse::<u16>()
        .ok()
        .filter(|port| *port != 0)
        .ok_or_else(|| "REMOTE_CODE_BRIDGE_PORT must be an integer between 1 and 65535".into())
}

fn value<F>(values: &BTreeMap<String, String>, environment: &F, key: &str) -> Option<String>
where
    F: Fn(&str) -> Option<String>,
{
    environment(key)
        .filter(|value| !value.is_empty())
        .or_else(|| values.get(key).filter(|value| !value.is_empty()).cloned())
}

fn token<F>(values: &BTreeMap<String, String>, environment: &F) -> Result<Option<String>, String>
where
    F: Fn(&str) -> Option<String>,
{
    let token = value(values, environment, "REMOTE_CODE_BRIDGE_TOKEN");
    if token.as_deref().is_some_and(|token| {
        token.len() != 64 || !token.bytes().all(|byte| byte.is_ascii_hexdigit())
    }) {
        return Err("REMOTE_CODE_BRIDGE_TOKEN must be exactly 64 ASCII hex characters".into());
    }
    Ok(token)
}

fn read_values(path: &Path) -> Result<BTreeMap<String, String>, String> {
    let contents = match fs::read_to_string(path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(error) => return Err(format!("could not read {}: {error}", path.display())),
    };

    Ok(contents
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                return None;
            }
            let (key, value) = line.split_once('=')?;
            Some((key.trim().to_owned(), value.trim().to_owned()))
        })
        .collect())
}
