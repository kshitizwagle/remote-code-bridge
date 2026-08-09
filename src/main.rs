use remote_code_bridge::{
    default_config_path, generate_token, parse_open_args, process_host_config,
    process_remote_config, read_remote_config, send_open_request, serve,
};
use std::env;
use std::path::Path;

fn main() {
    if let Err(error) = run() {
        eprintln!("remote-code-bridge: {error}");
        std::process::exit(2);
    }
}

fn run() -> Result<(), String> {
    let mut arguments = env::args();
    let program = arguments
        .next()
        .unwrap_or_else(|| "remote-code-bridge".into());
    let is_code = Path::new(&program)
        .file_name()
        .is_some_and(|name| name == "code");
    let command = if is_code {
        "open".into()
    } else {
        arguments.next().unwrap_or_else(|| "help".into())
    };
    let remaining: Vec<_> = arguments.collect();

    match command.as_str() {
        "serve" => serve(process_host_config()?),
        "open" => {
            let config_path = env::var_os("REMOTE_CODE_BRIDGE_CONFIG")
                .map(Into::into)
                .unwrap_or(default_config_path("remote.env")?);
            let config = if config_path == default_config_path("remote.env")? {
                process_remote_config()?
            } else {
                read_remote_config(&config_path, |key| env::var(key).ok())?
            };
            let payload = send_open_request(&config, parse_open_args(remaining)?)?;
            if !payload
                .get("ok")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
            {
                return Err(payload
                    .get("error")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("request failed")
                    .into());
            }
            if payload
                .get("dry_run")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false)
            {
                let command = payload
                    .get("command")
                    .and_then(serde_json::Value::as_array)
                    .ok_or_else(|| "host bridge returned invalid command".to_string())?;
                println!(
                    "dry-run command: {}",
                    command
                        .iter()
                        .filter_map(serde_json::Value::as_str)
                        .collect::<Vec<_>>()
                        .join(" ")
                );
            } else {
                println!("Opening VS Code on host");
            }
            Ok(())
        }
        "generate-token" => {
            if !remaining.is_empty() {
                return Err("generate-token does not accept arguments".into());
            }
            println!("{}", generate_token()?);
            Ok(())
        }
        _ => Err("usage: remote-code-bridge <serve|open|generate-token>".into()),
    }
}
