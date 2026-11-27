"""单行 JSON 日志与字段脱敏

禁止记录密码、CSRF、worker token、模型 Key、原始工具参数与完整工具输出。
未捕获异常只记录类型，不记录堆栈参数。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping

FIELD_ORDER = ("request_id", "run_id", "turn_id", "decision_id", "status",
               "latency_ms", "session_prefix", "method", "path", "http_status")
SENSITIVE_FRAGMENTS = (
    "password", "csrf", "token", "secret", "api_key", "apikey",
    "authorization", "cookie", "args", "content", "output",
)
_RESERVED = {"args", "msg", "levelname", "name", "exc_info", "exc_text"}


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS)


def _iso(created: float) -> str:
    return datetime.fromtimestamp(created, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _iso(record.created),
            "level": record.levelname,
            "component": getattr(record, "component", "temisguard"),
            "event": getattr(record, "event", None) or record.getMessage(),
        }
        for key in FIELD_ORDER:
            value = getattr(record, key, None)
            if value is not None and not _sensitive(key):
                payload[key] = value
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            for key, value in fields.items():
                if key in payload:
                    continue
                payload[key] = "***" if _sensitive(key) else value
        if record.exc_info:
            error = record.exc_info[0]
            payload["error"] = error.__name__ if error else "Exception"
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(level: str = "INFO", component: str = "temisguard") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if isinstance(handler.formatter, JsonFormatter):
            return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, *,
              level: int = logging.INFO, component: str | None = None,
              **fields: Any) -> None:
    extra: dict[str, Any] = {"event": event, "fields": fields}
    if component is not None:
        extra["component"] = component
    logger.log(level, event, extra=extra)


def session_prefix(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


__all__ = [
    "FIELD_ORDER",
    "JsonFormatter",
    "SENSITIVE_FRAGMENTS",
    "configure_logging",
    "log_event",
    "session_prefix",
]
