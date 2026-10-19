"""日志配置：文本或单行 JSON，重复初始化不叠加 handler。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

RESERVED_ATTRS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in RESERVED_ATTRS or key.startswith("_"):
                continue
            payload[key] = value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = [
            f"{key}={value}"
            for key, value in record.__dict__.items()
            if key not in RESERVED_ATTRS and not key.startswith("_")
        ]
        return f"{base} {' '.join(extras)}" if extras else base


def setup_logging(level: str = "INFO", json_format: bool = False, *, force: bool = False) -> None:
    root = logging.getLogger()
    if getattr(root, "_recon_configured", False) and not force:
        root.setLevel(level.upper())
        return

    handler = logging.StreamHandler()
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]
    root.setLevel(level.upper())
    root._recon_configured = True  # type: ignore[attr-defined]
    logging.getLogger("uvicorn.access").propagate = False
