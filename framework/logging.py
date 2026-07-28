"""Structured JSON-lines logging for the worker framework.

Every log line is a single JSON object on stdout: {"event": ..., "ts": ...,
**fields}. Simple to grep, simple to ship to any log aggregator, and easy
to assert on in tests without regex-parsing prose.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonLinesFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, level: str = "INFO", **fields: Any) -> None:
    logger.log(logging.getLevelName(level), event, extra={"fields": fields})
