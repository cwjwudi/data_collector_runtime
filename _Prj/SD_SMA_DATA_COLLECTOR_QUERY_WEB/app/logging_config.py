"""Central logging configuration for Query Web."""
from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path
from typing import Any

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_MAX_MESSAGE_LEN = 500
DEFAULT_FILE_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_FILE_BACKUP_COUNT = 5

_configured = False


def _resolve_level() -> str:
    raw = str(os.getenv("SD_SMA_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    if raw not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        return "INFO"
    return raw


def _resolve_max_message_len() -> int:
    raw = str(os.getenv("SD_SMA_LOG_MAX_MESSAGE_LEN", "") or "").strip()
    if not raw:
        return DEFAULT_MAX_MESSAGE_LEN
    try:
        return max(64, int(raw))
    except ValueError:
        return DEFAULT_MAX_MESSAGE_LEN


def _resolve_log_dir() -> Path | None:
    raw = str(os.getenv("SD_SMA_LOG_DIR", "") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


class TruncateMessageFilter(logging.Filter):
    """Keep log lines readable by truncating oversized messages (e.g. OPC cert dumps)."""

    def __init__(self, max_len: int = DEFAULT_MAX_MESSAGE_LEN) -> None:
        super().__init__()
        self.max_len = max(64, int(max_len))

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if len(msg) <= self.max_len:
            return True
        omitted = len(msg) - self.max_len
        record.msg = f"{msg[: self.max_len]}... [truncated {omitted} chars]"
        record.args = ()
        return True


def build_logging_config(level: str | None = None) -> dict[str, Any]:
    resolved = (level or _resolve_level()).upper()
    max_message_len = _resolve_max_message_len()
    formatter = {
        "format": LOG_FORMAT,
        "datefmt": LOG_DATE_FORMAT,
    }
    handlers: dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["truncate"],
            "stream": "ext://sys.stderr",
        },
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "filters": ["truncate"],
            "stream": "ext://sys.stdout",
        },
    }
    root_handlers = ["console"]
    app_handlers = ["console"]

    log_dir = _resolve_log_dir()
    if log_dir is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "standard",
            "filters": ["truncate"],
            "filename": str(log_dir / "app.log"),
            "maxBytes": DEFAULT_FILE_MAX_BYTES,
            "backupCount": DEFAULT_FILE_BACKUP_COUNT,
            "encoding": "utf-8",
        }
        root_handlers = ["console", "file"]
        app_handlers = ["console", "file"]

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "truncate": {
                "()": "app.logging_config.TruncateMessageFilter",
                "max_len": max_message_len,
            }
        },
        "formatters": {
            "standard": formatter,
            "access": formatter,
        },
        "handlers": handlers,
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": resolved, "propagate": False},
            "uvicorn.error": {"level": resolved, "propagate": True},
            "uvicorn.access": {"handlers": ["access"], "level": resolved, "propagate": False},
            "app": {"handlers": app_handlers, "level": resolved, "propagate": False},
            # asyncua dumps full EndpointDescription + certificates at INFO
            "asyncua": {"level": "WARNING", "propagate": True},
            "asyncua.client": {"level": "WARNING", "propagate": True},
            "asyncua.uaprotocol": {"level": "WARNING", "propagate": True},
        },
        "root": {"level": resolved, "handlers": root_handlers},
    }


LOGGING_CONFIG = build_logging_config()


def setup_logging(level: str | None = None, *, force: bool = False) -> None:
    """配置全局日志；force=True 时忽略已配置标记强制重新配置（如测试中重绑 stderr）"""
    global _configured
    if _configured and not force:
        return
    logging.config.dictConfig(build_logging_config(level))
    _configured = True
