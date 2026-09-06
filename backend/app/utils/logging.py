"""Structured JSON-lines logging on stdout (§18)."""
import json
import logging
import sys
from datetime import UTC, datetime

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        return json.dumps(payload, default=str)


def get_logger(name: str = "vortex") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(name)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _CONFIGURED = True
    return logger


def json_log(event: str, **fields: object) -> None:
    get_logger().info(event, extra={"fields": fields})
