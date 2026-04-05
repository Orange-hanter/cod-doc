"""
Настройка логирования COD-DOC.

Форматы:
  text (default) — человекочитаемый для разработки
  json           — структурированный для Docker/Loki/Datadog

Управление:
  LOG_FORMAT=json   LOG_LEVEL=DEBUG  python -m cod_doc ...
  или через Config: config.log_format / config.log_level
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Форматтер для структурированных JSON-логов."""

    LEVEL_MAP = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warning",
        logging.ERROR: "error",
        logging.CRITICAL: "critical",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": self.LEVEL_MAP.get(record.levelno, record.levelname.lower()),
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Контекстные поля, добавленные через extra={}
        for key in ("project", "event_type", "task_id", "tool"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Человекочитаемый форматтер с цветом для терминала."""

    COLORS = {
        "DEBUG": "\033[36m",    # cyan
        "INFO": "\033[32m",     # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",    # red
        "CRITICAL": "\033[35m", # magenta
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        level = record.levelname
        if self.use_color:
            color = self.COLORS.get(level, "")
            level_str = f"{color}{level:<8}{self.RESET}"
        else:
            level_str = f"{level:<8}"

        name = record.name.replace("cod_doc.", "")
        msg = record.getMessage()

        # Доп. контекст
        ctx_parts = []
        for key in ("project", "task_id", "tool"):
            if hasattr(record, key):
                ctx_parts.append(f"{key}={getattr(record, key)}")
        ctx = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""

        line = f"{ts} {level_str} {name}{ctx}: {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(
    level: str | None = None,
    fmt: str | None = None,
) -> None:
    """
    Настроить логирование приложения.

    Args:
        level: DEBUG | INFO | WARNING | ERROR (env: LOG_LEVEL, default INFO)
        fmt:   text | json (env: LOG_FORMAT, default text)
    """
    level_str = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    fmt_str = (fmt or os.environ.get("LOG_FORMAT", "text")).lower()

    log_level = getattr(logging, level_str, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    if fmt_str == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root = logging.getLogger()
    root.setLevel(log_level)

    # Убрать дублирующие хэндлеры
    root.handlers.clear()
    root.addHandler(handler)

    # Подавить шумные библиотеки
    for noisy in ("httpx", "httpcore", "openai._base_client", "chromadb", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Получить логгер с именем в пространстве cod_doc.*"""
    return logging.getLogger(f"cod_doc.{name}" if not name.startswith("cod_doc") else name)
