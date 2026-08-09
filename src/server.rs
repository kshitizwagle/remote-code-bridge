use crate::config::HostConfig;
use crate::protocol::{handle_request, HttpRequest, Response};
use crate::MAX_REQUEST_BYTES;
use std::collections::BTreeMap;
use std::io::{BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::Duration;

const WORKER_COUNT: usize = 2;
const QUEUE_CAPACITY: usize = 2;
const WORKER_STACK_BYTES: usize = 256 * 1024;

pub fn serve(config: HostConfig) -> Result<(), String> {
    if config
        .token
        .as_deref()
        .filter(|token| !token.is_empty())
        .is_none()
    {
        return Err("REMOTE_CODE_BRIDGE_TOKEN is required".into());
    }
    if config.bind != "127.0.0.1" {
        return Err("Refusing to bind to non-localhost address".into());
    }
    let listener = TcpListener::bind((config.bind.as_str(), config.port)).map_err(|error| {
        format!(
            "could not listen on {}:{}: {error}",
            config.bind, config.port
        )
    })?;
    eprintln!(
        "remote-code-bridge listening on http://{}:{}",
        config.bind, config.port
    );
    let (sender, receiver) = mpsc::sync_channel(QUEUE_CAPACITY);
    let receiver = Arc::new(Mutex::new(receiver));
    for worker in 0..WORKER_COUNT {
        let receiver = Arc::clone(&receiver);
        let config = config.clone();
        thread::Builder::new()
            .name(format!("remote-code-bridge-{worker}"))
            .stack_size(WORKER_STACK_BYTES)
            .spawn(move || loop {
                let stream = match receiver.lock().expect("worker receiver lock").recv() {
                    Ok(stream) => stream,
                    Err(_) => return,
                };
                if let Err(error) = handle_stream(stream, &config) {
                    eprintln!("remote-code-bridge: {error}");
                }
            })
            .map_err(|error| format!("could not start worker: {error}"))?;
    }
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => match sender.try_send(stream) {
                Ok(()) => {}
                Err(mpsc::TrySendError::Full(stream)) => write_busy_response(stream),
                Err(mpsc::TrySendError::Disconnected(_)) => {
                    return Err("worker pool stopped".into())
                }
            },
            Err(error) => eprintln!("remote-code-bridge: accepting connection failed: {error}"),
        }
    }
    Ok(())
}

fn handle_stream(stream: TcpStream, config: &HostConfig) -> Result<(), String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| format!("could not set read timeout: {error}"))?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|error| format!("could not set write timeout: {error}"))?;
    let mut writer = stream
        .try_clone()
        .map_err(|error| format!("could not clone connection: {error}"))?;
    let response = match read_request(stream) {
        Ok(request) => handle_request(config, request),
        Err(error) => Response::error(400, &error),
    };
    write_response(&mut writer, response)
        .map_err(|error| format!("could not write response: {error}"))
}

fn read_request(stream: TcpStream) -> Result<HttpRequest, String> {
    read_request_with_deadline(stream, Duration::from_secs(5))
}

fn read_request_with_deadline(stream: TcpStream, timeout: Duration) -> Result<HttpRequest, String> {
    let deadline = std::time::Instant::now() + timeout;
    let mut reader = BufReader::new(stream);
    let first_line = read_line_limited(&mut reader, 1024, deadline)?;
    let mut parts = first_line.split_whitespace();
    let method = parts
        .next()
        .ok_or_else(|| "invalid HTTP request".to_string())?
        .to_owned();
    let path = parts
        .next()
        .ok_or_else(|| "invalid HTTP request".to_string())?
        .to_owned();
    if parts.next().is_none() {
        return Err("invalid HTTP request".into());
    }
    let mut headers = BTreeMap::new();
    let mut header_bytes = first_line.len();
    loop {
        let line = read_line_limited(&mut reader, 1024, deadline)?;
        let bytes = line.len();
        header_bytes += bytes;
        if header_bytes > 16 * 1024 {
            return Err("request headers are too large".into());
        }
        if line == "\r\n" || line == "\n" {
            break;
        }
        let (name, value) = line
            .trim_end()
            .split_once(':')
            .ok_or_else(|| "invalid HTTP header".to_string())?;
        headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_owned());
    }
    let length = match headers.get("content-length") {
        Some(length) => length
            .parse::<usize>()
            .map_err(|_| "invalid content length".to_string())?,
        None if method == "GET" => 0,
        None => return Err("invalid request size".into()),
    };
    if length > MAX_REQUEST_BYTES || (method == "POST" && length == 0) {
        return Err("invalid request size".into());
    }
    let mut body = vec![0; length];
    read_body_limited(&mut reader, &mut body, deadline)?;
    Ok(HttpRequest {
        method,
        path,
        headers,
        body,
    })
}

fn read_line_limited(
    reader: &mut BufReader<TcpStream>,
    limit: usize,
    deadline: std::time::Instant,
) -> Result<String, String> {
    let mut bytes = Vec::with_capacity(128);
    loop {
        ensure_before(deadline)?;
        let mut byte = [0_u8; 1];
        let read = reader.read(&mut byte).map_err(|_| timed_error(deadline))?;
        ensure_after(deadline)?;
        if read == 0 {
            return Err("invalid HTTP request".into());
        }
        if bytes.len() == limit {
            return Err("request headers are too large".into());
        }
        bytes.push(byte[0]);
        if byte[0] == b'\n' {
            return String::from_utf8(bytes).map_err(|_| "invalid HTTP request".into());
        }
    }
}

fn read_body_limited(
    reader: &mut BufReader<TcpStream>,
    body: &mut [u8],
    deadline: std::time::Instant,
) -> Result<(), String> {
    let mut offset = 0;
    while offset < body.len() {
        ensure_before(deadline)?;
        let read = reader
            .read(&mut body[offset..])
            .map_err(|_| timed_error(deadline))?;
        ensure_after(deadline)?;
        if read == 0 {
            return Err("invalid request body".into());
        }
        offset += read;
    }
    Ok(())
}

fn ensure_before(deadline: std::time::Instant) -> Result<(), String> {
    if std::time::Instant::now() >= deadline {
        return Err("request timed out".into());
    }
    Ok(())
}

fn ensure_after(deadline: std::time::Instant) -> Result<(), String> {
    ensure_before(deadline)
}

fn timed_error(deadline: std::time::Instant) -> String {
    if std::time::Instant::now() >= deadline {
        "request timed out".into()
    } else {
        "invalid HTTP request".into()
    }
}

fn write_busy_response(mut stream: TcpStream) {
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    let response = Response::error(503, "server busy");
    let _ = write_response(&mut stream, response);
}

fn write_response(stream: &mut TcpStream, response: Response) -> std::io::Result<()> {
    let reason = match response.status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        503 => "Service Unavailable",
        _ => "Internal Server Error",
    };
    write!(
        stream,
        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        response.status,
        reason,
        response.body.len()
    )?;
    stream.write_all(&response.body)
}

#[cfg(test)]
mod tests {
    use super::{
        handle_stream, read_request, read_request_with_deadline, serve, write_busy_response,
    };
    use crate::config::HostConfig;
    use crate::protocol::handle_request;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;
    use std::time::Duration;

    #[test]
    fn request_parser_normalizes_headers_and_reads_json_body() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test listener");
        let address = listener.local_addr().unwrap();
        let writer = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(b"POST /open HTTP/1.1\r\nAuthorization: Bearer token\r\nContent-Length: 2\r\n\r\n{}")
        });
        let (stream, _) = listener.accept().unwrap();
        let request = read_request(stream).unwrap();
        writer.join().unwrap().unwrap();

        assert_eq!(request.headers["authorization"], "Bearer token");
        assert_eq!(request.body, b"{}");
    }

    #[test]
    fn bodyless_health_check_reaches_the_route_handler() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test listener");
        let address = listener.local_addr().unwrap();
        let writer = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        });
        let (stream, _) = listener.accept().unwrap();
        let request = read_request(stream).unwrap();
        writer.join().unwrap().unwrap();

        let response = handle_request(
            &HostConfig {
                bind: "127.0.0.1".into(),
                port: 39731,
                token: None,
                code_bin: "code".into(),
                default_host: None,
                allowed_hosts: None,
                dry_run: true,
            },
            request,
        );
        assert_eq!(response.status, 200);
    }

    #[test]
    fn request_deadline_rejects_a_slow_drip_header() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test listener");
        let address = listener.local_addr().unwrap();
        let writer = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            stream.write_all(b"GET /healthz HTTP/1.1\r\n").unwrap();
            thread::sleep(Duration::from_millis(30));
            stream.write_all(b"Host: 127.0.0.1\r\n\r\n")
        });
        let (stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_millis(100)))
            .unwrap();
        let error = match read_request_with_deadline(stream, Duration::from_millis(10)) {
            Err(error) => error,
            Ok(_) => panic!("slow request was accepted"),
        };
        writer.join().unwrap().unwrap();

        assert_eq!(error, "request timed out");
    }

    #[test]
    fn stream_handler_writes_a_health_response() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let config = HostConfig {
            bind: "127.0.0.1".into(),
            port: address.port(),
            token: Some("test-token".into()),
            code_bin: "code".into(),
            default_host: None,
            allowed_hosts: None,
            dry_run: true,
        };
        let handler = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            handle_stream(stream, &config)
        });
        let mut client = TcpStream::connect(address).unwrap();
        client
            .write_all(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            .unwrap();
        let mut response = String::new();
        client.read_to_string(&mut response).unwrap();
        handler.join().unwrap().unwrap();

        assert!(response.starts_with("HTTP/1.1 200 OK"));
    }

    #[test]
    fn serve_accepts_a_connection_on_its_configured_loopback_port() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        thread::spawn(move || {
            let _ = serve(HostConfig {
                bind: "127.0.0.1".into(),
                port,
                token: Some("test-token".into()),
                code_bin: "code".into(),
                default_host: None,
                allowed_hosts: None,
                dry_run: true,
            });
        });
        let deadline = std::time::Instant::now() + Duration::from_secs(1);
        loop {
            if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
                stream
                    .write_all(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                    .unwrap();
                let mut response = String::new();
                stream.read_to_string(&mut response).unwrap();
                assert!(response.starts_with("HTTP/1.1 200 OK"));
                return;
            }
            assert!(std::time::Instant::now() < deadline, "server did not start");
            thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn saturated_connections_receive_service_unavailable() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test listener");
        let address = listener.local_addr().unwrap();
        let writer = thread::spawn(move || {
            let mut stream = TcpStream::connect(address).unwrap();
            let mut response = String::new();
            stream.read_to_string(&mut response).unwrap();
            response
        });
        let (stream, _) = listener.accept().unwrap();
        write_busy_response(stream);
        let response = writer.join().unwrap();

        assert!(response.starts_with("HTTP/1.1 503 Service Unavailable\r\n"));
        assert!(response.contains("server busy"));
    }
}
