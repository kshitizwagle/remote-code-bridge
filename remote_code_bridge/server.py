"""The host bridge: a localhost HTTP server, plus the SSH tunnel that makes it reachable."""

from __future__ import annotations

import logging
import os
import signal
import socket
import socketserver
import threading

from remote_code_bridge import BridgeError
from remote_code_bridge.config import HostConfig
from remote_code_bridge.httpio import DeadlineReader, HttpError, format_response, read_body, read_head
from remote_code_bridge.protocol import HttpRequest, Launcher, Response, handle_request
from remote_code_bridge.tunnel import TunnelSupervisor

log = logging.getLogger(__name__)
MAX_ACTIVE = 4  # requests handled at the same time
MAX_WAITING = 64  # connections allowed to wait for a free slot; more than that are turned away
REQUEST_TIMEOUT = 5.0


class BridgeServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets another process steal the port, so only use it on POSIX.
    allow_reuse_address = os.name != "nt"
    # The default backlog of 5 makes macOS drop connections when many `code` commands arrive at once.
    request_queue_size = 64

    def __init__(self, config: HostConfig, tunnel: TunnelSupervisor | None = None):
        self.config = config
        self.tunnel = tunnel
        self.launcher = Launcher()
        self._active = threading.BoundedSemaphore(MAX_ACTIVE)
        self._admitted = threading.BoundedSemaphore(MAX_ACTIVE + MAX_WAITING)
        super().__init__((config.bind, config.port), _Handler)

    def process_request(self, request, client_address):  # type: ignore[no-untyped-def]
        if not self._admitted.acquire(blocking=False):
            self._busy(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):  # type: ignore[no-untyped-def]
        # A burst (say, `code .` in a dozen terminals at once) queues briefly instead of failing.
        try:
            if self._active.acquire(timeout=REQUEST_TIMEOUT):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self._active.release()
            else:
                self._busy(request)
        finally:
            self._admitted.release()

    def _busy(self, request: socket.socket) -> None:
        _send(request, Response.error(503, "server busy"), timeout=1.0)
        self.shutdown_request(request)


class _Handler(socketserver.BaseRequestHandler):
    server: BridgeServer

    def handle(self) -> None:
        try:
            request = read_request(self.request, REQUEST_TIMEOUT)
        except HttpError as error:
            response = Response.error(400, str(error))
        else:
            tunnel = self.server.tunnel
            response = handle_request(
                self.server.config, request, self.server.launcher, tunnel.status if tunnel else None
            )
            if request.path == "/open":
                log.info("open %s -> %s", request.method, response.status)
        _send(self.request, response, timeout=REQUEST_TIMEOUT)


def read_request(sock: socket.socket, timeout: float) -> HttpRequest:
    reader = DeadlineReader(sock, timeout)
    start, headers = read_head(reader)
    parts = start.split()
    if len(parts) != 3:
        raise HttpError("invalid HTTP request")
    method, path, _version = parts
    return HttpRequest(method, path, headers, read_body(reader, method, headers))


def _send(sock: socket.socket, response: Response, timeout: float) -> None:
    try:
        sock.settimeout(timeout)
        sock.sendall(format_response(response.status, response.body()))
    except OSError as error:
        log.warning("could not write response: %s", error)


def serve(config: HostConfig) -> None:
    if not config.token:
        raise BridgeError("REMOTE_CODE_BRIDGE_TOKEN is required")
    if config.bind != "127.0.0.1":
        raise BridgeError("Refusing to bind to non-localhost address")

    tunnel = None
    if config.tunnel_enabled and config.tunnel_alias and config.tunnel_socket:
        tunnel = TunnelSupervisor(config.tunnel_alias, config.tunnel_socket, config.port)
    elif config.tunnel_enabled:
        log.warning("tunnel disabled: REMOTE_CODE_BRIDGE_TUNNEL_ALIAS/_SOCKET are not configured")

    try:
        server = BridgeServer(config, tunnel)
    except OSError as error:
        raise BridgeError(f"could not listen on {config.bind}:{config.port}: {error}") from error

    def stop(_signum: int, _frame: object) -> None:
        # shutdown() waits for serve_forever() to return, so it can't run on this (main) thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("listening on http://%s:%d", config.bind, config.port)
    if tunnel:
        tunnel.start()
    try:
        server.serve_forever()
    finally:
        if tunnel:
            tunnel.stop()
        server.server_close()
        log.info("stopped")
