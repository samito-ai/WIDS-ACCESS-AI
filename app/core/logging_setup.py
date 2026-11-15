"""Strukturiertes Logging (JSON-Zeilen) mit Schwärzung von Geheimnissen."""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import paths

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|x-api-key|bearer)(\"?\s*[:=]\s*\"?)([^\s\"',}]{6,})"),
]
_registered_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Merkt sich einen konkreten Geheimwert, der niemals im Log erscheinen darf."""
    if value and len(value) >= 6:
        _registered_secrets.add(value)


def redact(text: str) -> str:
    """Entfernt API-Schlüssel und ähnliche Geheimnisse aus einem Text."""
    for secret in _registered_secrets:
        if secret in text:
            text = text.replace(secret, "***")
    text = _SECRET_PATTERNS[0].sub("sk-ant-***", text)
    text = _SECRET_PATTERNS[1].sub("sk-***", text)
    text = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "thread": record.threadName,
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload["data"] = json.loads(redact(json.dumps(extra, default=str, ensure_ascii=False)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_logging(level: str = "INFO", *, log_dir: Path | None = None, console: bool = False) -> Path:
    """Richtet das Logging ein und gibt den Pfad der Logdatei zurück (idempotent)."""
    global _configured
    directory = log_dir or paths.logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "wids.log.jsonl"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if _configured:
        return log_file
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        stream.addFilter(RedactingFilter())
        root.addHandler(stream)
    # Geschwätzige Bibliotheken zähmen (und damit auch Header-Logs vermeiden)
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic", "comtypes", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
    return log_file


def set_level(level: str) -> None:
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **data: Any) -> None:
    """Loggt eine Meldung mit strukturierten Zusatzdaten."""
    logger.log(level, message, extra={"data": data})
