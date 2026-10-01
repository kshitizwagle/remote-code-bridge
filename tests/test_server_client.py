import json
import socket
import sys
import threading
import time

import pytest
from conftest import TOKEN

from remote_code_bridge import BridgeError
from remote_code_bridge.client import NO_TUNNEL, call, open_in_vscode, parse_open_args, resolve_remote_path
from remote_code_bridge.config import HostConfig, RemoteConfig
from remote_code_bridge.httpio import DeadlineReader, HttpError
from remote_code_bridge.server import BridgeServer, read_request

unix_only = pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="needs AF_UNIX")


@pytest.fixture
def server():
    config = HostConfig(port=0, token=TOKEN, code_bin="code", default_host="devbox", dry_run=True)
    bridge = BridgeServer(config)
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    yield bridge
    bridge.shutdown()
    bridge.server_close()


def port_of(bridge):
    return bridge.server_address[1]


def raw_exchange(port, data, delay=0.0):
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        for chunk in data if isinstance(data, list) else [data]:
            sock.sendall(chunk)
            time.sleep(delay)
        return DeadlineReader(sock, 10).read_to_end(1 << 20).decode()


def test_health_over_http(server):
    response = raw_exchange(port_of(server), b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    assert response.startswith("HTTP/1.1 200 OK\r\n")
    assert json.loads(response.split("\r\n\r\n", 1)[1])["tunnel"] == {"state": "disabled"}


def test_client_round_trip_over_tcp(server):
    config = RemoteConfig(port=port_of(server), host_alias="devbox", token=TOKEN)
    assert open_in_vscode(config, ["--reuse-window", "/srv/project"]) == (
        "dry-run command: code --reuse-window --remote ssh-remote+devbox /srv/project"
    )


def test_client_reports_host_errors(server):
    config = RemoteConfig(port=port_of(server), host_alias="devbox", token="f" * 64)
    with pytest.raises(BridgeError, match="unauthorized"):
        open_in_vscode(config, ["/srv"])


@unix_only
def test_client_round_trip_over_a_unix_socket(server, tmp_path):
    """The remote client talks to a Unix socket; here a tiny relay plays the part of `ssh -R`."""
    path = str(tmp_path / "bridge.sock")
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    listener.listen()

    def relay():
        conn, _ = listener.accept()
        upstream = socket.create_connection(("127.0.0.1", port_of(server)))
        upstream.sendall(conn.recv(65536))
        while True:
            data = upstream.recv(65536)
            if not data:
                break
            conn.sendall(data)
        conn.close()
        upstream.close()

    threading.Thread(target=relay, daemon=True).start()
    config = RemoteConfig(host_alias="devbox", token=TOKEN, socket=path)
    assert open_in_vscode(config, ["-n", "/x"]) == "dry-run command: code -n --remote ssh-remote+devbox /x"
    listener.close()


@unix_only
def test_missing_socket_explains_the_tunnel(tmp_path):
    config = RemoteConfig(host_alias="devbox", token=TOKEN, socket=str(tmp_path / "nope.sock"))
    with pytest.raises(BridgeError) as error:
        open_in_vscode(config, ["/x"])
    assert str(error.value) == NO_TUNNEL


def test_unreachable_port_explains_the_tunnel():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(BridgeError, match="no tunnel"):
        call(RemoteConfig(port=port), "GET", "/healthz")


def test_slow_client_does_not_block_health(server):
    slow = socket.create_connection(("127.0.0.1", port_of(server)))
    slow.sendall(b"POST /open HTTP/1.1\r\n")
    started = time.monotonic()
    response = raw_exchange(port_of(server), b"GET /healthz HTTP/1.1\r\n\r\n")
    assert response.startswith("HTTP/1.1 200")
    assert time.monotonic() - started < 1
    slow.close()


def test_request_deadline_rejects_slow_drip():
    a, b = socket.socketpair()
    a.sendall(b"GET /healthz HTTP/1.1\r\n")
    with pytest.raises(HttpError, match="timed out"):
        read_request(b, timeout=0.1)
    a.close()
    b.close()


@pytest.mark.parametrize(
    "raw,message",
    [
        (b"GARBAGE\r\n\r\n", "invalid HTTP request"),
        (b"GET / HTTP/1.1\r\nNoColon\r\n\r\n", "invalid HTTP header"),
        (b"POST /open HTTP/1.1\r\n\r\n", "invalid request size"),
        (b"POST /open HTTP/1.1\r\nContent-Length: x\r\n\r\n", "invalid content length"),
        (b"POST /open HTTP/1.1\r\nContent-Length: 70000\r\n\r\n", "invalid request size"),
        (b"GET /" + b"a" * 2000 + b" HTTP/1.1\r\n\r\n", "too large"),
        (b"GET / HTTP/1.1\r\n" + b"X: y\r\n" * 5000 + b"\r\n", "too large"),
        (b"POST /open HTTP/1.1\r\nContent-Length: 10\r\n\r\nabc", "invalid request body"),
    ],
    ids=["start-line", "header", "no-length", "bad-length", "big-body", "long-line", "many-headers", "short-body"],
)
def test_malformed_requests(raw, message):
    a, b = socket.socketpair()
    a.sendall(raw)
    a.shutdown(socket.SHUT_WR)
    with pytest.raises(HttpError, match=message):
        read_request(b, timeout=2)
    a.close()
    b.close()


def test_bad_request_gets_400_over_the_wire(server):
    assert raw_exchange(port_of(server), b"GARBAGE\r\n\r\n").startswith("HTTP/1.1 400 Bad Request")


def test_saturated_server_answers_busy(server, monkeypatch):
    for _ in range(4):  # use up every connection slot
        server._slots.acquire()
    try:
        response = raw_exchange(port_of(server), b"GET /healthz HTTP/1.1\r\n\r\n")
    finally:
        for _ in range(4):
            server._slots.release()
    assert response.startswith("HTTP/1.1 503 Service Unavailable")
    assert "server busy" in response


def test_parse_open_args():
    request = parse_open_args(["--reuse-window", "-g", "src/main.py"], cwd="/home/u/repo")
    assert request.args == ["--reuse-window", "-g"]
    assert request.path == "/home/u/repo/src/main.py"
    assert parse_open_args([], cwd="/home/u").path == "/home/u"
    assert parse_open_args(["--", "-weird-name"], cwd="/w").path == "/w/-weird-name"


@pytest.mark.parametrize(
    "args,message",
    [
        (["--install-extension", "x"], "unsupported code flag for remote bridge: --install-extension"),
        (["--verbose"], "unsupported flag: --verbose"),
        (["one", "two"], "only one path is supported"),
        (["one", "--", "two"], "only one path is supported"),
    ],
)
def test_parse_open_args_errors(args, message):
    with pytest.raises(BridgeError, match=message):
        parse_open_args(args, cwd="/")


def test_resolve_remote_path():
    assert resolve_remote_path("../b/./c", "/home/u/a") == "/home/u/b/c"
    assert resolve_remote_path("/x/../y") == "/y"
    assert resolve_remote_path("not-created-yet").endswith("not-created-yet")


def test_client_requires_alias_and_token():
    with pytest.raises(BridgeError, match="HOST_ALIAS"):
        open_in_vscode(RemoteConfig(token=TOKEN), ["/x"])
    with pytest.raises(BridgeError, match="TOKEN"):
        open_in_vscode(RemoteConfig(host_alias="devbox"), ["/x"])
