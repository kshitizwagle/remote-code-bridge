"""Logging for the host service: a small rotating file, plus stderr when there is one."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


class _Redact(logging.Filter):
    def __init__(self, secret: str | None):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret:
            message = record.getMessage()
            if self.secret in message:
                record.msg, record.args = message.replace(self.secret, "<redacted>"), None
        return True


def setup(path: Path, secret: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError as error:
        print(f"remote-code-bridge: cannot write log {path}: {error}", file=sys.stderr)
    if sys.stderr is not None:  # pythonw.exe on Windows has no stderr
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(_Redact(secret))
        root.addHandler(handler)
