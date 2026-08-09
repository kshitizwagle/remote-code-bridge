use crate::config::RemoteConfig;
use crate::protocol::OpenRequest;
use crate::MAX_REQUEST_BYTES;
use std::env;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

pub fn parse_open_args<I, S>(arguments: I) -> Result<OpenRequest, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let mut flags = Vec::new();
    let mut path = None;
    let mut after_separator = false;
    for raw in arguments {
        let raw = raw.as_ref();
        if after_separator {
            if path.replace(raw.to_owned()).is_some() {
                return Err("only one path is supported".into());
            }
            continue;
        }
        match raw {
            "-r" | "--reuse-window" | "-n" | "--new-window" | "-g" | "--goto" => {
                flags.push(raw.to_owned())
            }
            "--" => after_separator = true,
            "--wait"
            | "--diff"
            | "--merge"
            | "--install-extension"
            | "--uninstall-extension"
            | "--list-extensions" => {
                return Err(format!("unsupported code flag for remote bridge: {raw}"));
            }
            _ if raw.starts_with('-') => return Err(format!("unsupported flag: {raw}")),
            _ if path.replace(raw.to_owned()).is_some() => {
                return Err("only one path is supported".into())
            }
            _ => {}
        }
    }
    Ok(OpenRequest {
        host: None,
        path: resolve_remote_path(path.as_deref().unwrap_or("."))?
            .display()
            .to_string(),
        args: flags,
    })
}

pub fn resolve_remote_path(input: &str) -> Result<PathBuf, String> {
    let path = Path::new(input);
    if path.is_absolute() {
        return Ok(path.to_path_buf());
    }
    env::current_dir()
        .map(|directory| {
            if input == "." {
                directory
            } else {
                directory.join(path)
            }
        })
        .map_err(|error| format!("could not resolve current directory: {error}"))
}

pub fn send_open_request(
    config: &RemoteConfig,
    open: OpenRequest,
) -> Result<serde_json::Value, String> {
    let host = config
        .host_alias
        .as_deref()
        .filter(|host| !host.is_empty())
        .ok_or_else(|| "REMOTE_CODE_BRIDGE_HOST_ALIAS is not set".to_string())?;
    let token = config
        .token
        .as_deref()
        .filter(|token| !token.is_empty())
        .ok_or_else(|| "REMOTE_CODE_BRIDGE_TOKEN is not set".to_string())?;
    let body = serde_json::to_vec(&OpenRequest {
        host: Some(host.to_owned()),
        ..open
    })
    .map_err(|error| format!("could not encode request: {error}"))?;
    if body.len() > MAX_REQUEST_BYTES {
        return Err("request is too large".into());
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    let endpoint = std::net::SocketAddr::from(([127, 0, 0, 1], config.port));
    let mut stream =
        TcpStream::connect_timeout(&endpoint, Duration::from_secs(5)).map_err(reach_error)?;
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(reach_error)?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(reach_error)?;
    let head = format!(
        "POST /open HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nAuthorization: Bearer {token}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(&body))
        .map_err(reach_error)?;
    let response = read_response_limited(&mut stream, deadline)?;
    let (_, body) = split_http_response(&response)?;
    serde_json::from_slice(body).map_err(|_| "host bridge returned invalid JSON".into())
}

fn read_response_limited(stream: &mut TcpStream, deadline: Instant) -> Result<Vec<u8>, String> {
    let mut response = Vec::new();
    let mut buffer = [0_u8; 1024];
    loop {
        if Instant::now() >= deadline {
            return Err("host bridge response timed out".into());
        }
        let read = stream.read(&mut buffer).map_err(|error| {
            if Instant::now() >= deadline {
                "host bridge response timed out".into()
            } else {
                reach_error(error)
            }
        })?;
        if Instant::now() >= deadline {
            return Err("host bridge response timed out".into());
        }
        if read == 0 {
            return Ok(response);
        }
        response.extend_from_slice(&buffer[..read]);
        if response.len() > MAX_REQUEST_BYTES {
            return Err("host bridge response is too large".into());
        }
    }
}

fn split_http_response(response: &[u8]) -> Result<(u16, &[u8]), String> {
    let Some(boundary) = response.windows(4).position(|window| window == b"\r\n\r\n") else {
        return Err("host bridge returned an invalid HTTP response".into());
    };
    let header = std::str::from_utf8(&response[..boundary])
        .map_err(|_| "host bridge returned invalid HTTP headers")?;
    let status = header
        .split_whitespace()
        .nth(1)
        .and_then(|raw| raw.parse().ok())
        .ok_or_else(|| "host bridge returned an invalid HTTP status".to_string())?;
    Ok((status, &response[boundary + 4..]))
}

fn reach_error(error: std::io::Error) -> String {
    format!("could not reach host bridge. Check SSH RemoteForward and that remote-code-bridge is running on the host. Details: {error}")
}

#[cfg(test)]
mod tests {
    use super::read_response_limited;
    use std::io::Write;
    use std::net::{TcpListener, TcpStream};
    use std::thread;
    use std::time::{Duration, Instant};

    #[test]
    fn response_deadline_rejects_a_slow_drip() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let writer = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(b"H").unwrap();
            thread::sleep(Duration::from_millis(30));
            stream.write_all(b"TTP/1.1 200 OK\r\n\r\n")
        });
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_millis(100)))
            .unwrap();
        let error = read_response_limited(&mut stream, Instant::now() + Duration::from_millis(10))
            .unwrap_err();
        writer.join().unwrap().unwrap();

        assert_eq!(error, "host bridge response timed out");
    }
}
