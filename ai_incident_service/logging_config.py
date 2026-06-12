"""Structured logging with secret redaction and request correlation IDs.

Replaces ad-hoc ``print()`` calls so logs are:
  - machine-parseable (JSON) for log aggregators,
  - correlated per request via a ``request_id`` carried in a context variable,
  - scrubbed of registered secrets (API keys, SMTP passwords, auth tokens) so
    credentials never leak into stdout or a log store.
"""

import json
import logging
import sys
from contextvars import ContextVar
from typing import Iterable, List

# Carries a per-request correlation id through async call stacks.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Secret substrings to redact from every emitted log line.
_SECRETS: List[str] = []
_REDACTION = "***REDACTED***"


def register_secrets(values: Iterable[str]) -> None:
    """Register secret values that must never appear in logs."""
    for value in values:
        if value and len(value) >= 4:
            _SECRETS.append(value)


def _redact(message: str) -> str:
    for secret in _SECRETS:
        if secret in message:
            message = message.replace(secret, _REDACTION)
    return message


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "message": _redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = _redact(self.formatException(record.exc_info))
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z')} "
            f"{record.levelname:<7} [{request_id_var.get()}] "
            f"{record.name}: {record.getMessage()}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return _redact(base)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger once at startup."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # Keep uvicorn access noise out of structured logs unless DEBUG is requested.
    logging.getLogger("uvicorn.access").setLevel(
        logging.INFO if level == "DEBUG" else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
