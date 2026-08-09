mod client;
mod config;
mod protocol;
mod server;
mod update;

pub use client::{parse_open_args, resolve_remote_path, send_open_request};
pub use config::{
    default_config_path, process_host_config, process_remote_config, read_host_config,
    read_remote_config, HostConfig, RemoteConfig,
};
pub use protocol::{build_code_command, handle_request, HttpRequest, OpenRequest, Response};
pub use server::serve;
pub use update::run_update;

pub const APP_NAME: &str = "remote-code-bridge";
pub const DEFAULT_PORT: u16 = 39731;
pub const MAX_REQUEST_BYTES: usize = 64 * 1024;

pub fn generate_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes)
        .map_err(|error| format!("could not generate token: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}
