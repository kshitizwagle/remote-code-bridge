"""A deliberately small HTTP/1.1 reader and writer, shared by the server and the client.

The stdlib HTTP server only has a per-read timeout, so a client dripping one byte every few
seconds could hold a worker forever. Everything here works against one overall deadline.
"""

from __future__ import annotations

import socket
import time

from remote_code_bridge import MAX_REQUEST_BYTES

MAX_LINE_BYTES = 1024
MAX_HEADER_BYTES = 16 * 1024
REASONS = {
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    503: "Service Unavailable",
}


class HttpError(Exception):
    pass


class DeadlineReader:
    def __init__(self, sock: socket.socket, timeout: float, timeout_message: str = "request timed out"):
        self.sock = sock
        self.deadline = time.monotonic() + timeout
        self.timeout_message = timeout_message
        self.buffer = b""

    def _fill(self, eof_message: str) -> bool:
        """Read more bytes. At end of stream, raise `eof_message`, or return False if it is empty."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise HttpError(self.timeout_message)
        self.sock.settimeout(remaining)
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout as error:
            raise HttpError(self.timeout_message) from error
        except OSError as error:
            raise HttpError(eof_message or str(error)) from error
        if not chunk:
            if eof_message:
                raise HttpError(eof_message)
            return False
        self.buffer += chunk
        return True

    def readline(self) -> bytes:
        while b"\n" not in self.buffer[:MAX_LINE_BYTES]:
            if len(self.buffer) >= MAX_LINE_BYTES:
                raise HttpError("request headers are too large")
            self._fill("invalid HTTP request")
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line.rstrip(b"\r")

    def read_exactly(self, length: int) -> bytes:
        while len(self.buffer) < length:
            self._fill("invalid request body")
        data, self.buffer = self.buffer[:length], self.buffer[length:]
        return data

    def read_to_end(self, limit: int) -> bytes:
        while self._fill(eof_message=""):
            if len(self.buffer) > limit:
                raise HttpError("host bridge response is too large")
        return self.buffer


def read_head(reader: DeadlineReader) -> tuple[str, dict[str, str]]:
    """Read the start line and headers. Header names come back lower-case."""
    try:
        start = reader.readline().decode("utf-8")
    except UnicodeDecodeError as error:
        raise HttpError("invalid HTTP request") from error
    headers: dict[str, str] = {}
    total = len(start)
    while True:
        line = reader.readline()
        total += len(line)
        if total > MAX_HEADER_BYTES:
            raise HttpError("request headers are too large")
        if not line:
            return start, headers
        name, colon, value = line.decode("latin-1").partition(":")
        if not colon:
            raise HttpError("invalid HTTP header")
        headers[name.strip().lower()] = value.strip()


def read_body(reader: DeadlineReader, method: str, headers: dict[str, str]) -> bytes:
    raw = headers.get("content-length")
    if raw is None:
        if method == "GET":
            return b""
        raise HttpError("invalid request size")
    if not raw.isdigit():
        raise HttpError("invalid content length")
    length = int(raw)
    if length > MAX_REQUEST_BYTES or (method == "POST" and length == 0):
        raise HttpError("invalid request size")
    return reader.read_exactly(length)


def format_response(status: int, body: bytes) -> bytes:
    head = (
        f"HTTP/1.1 {status} {REASONS.get(status, 'Internal Server Error')}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


def format_request(method: str, path: str, body: bytes, token: str = "") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1", "Connection: close"]
    if token:
        lines.append(f"Authorization: Bearer {token}")
    if body or method == "POST":
        lines += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body
