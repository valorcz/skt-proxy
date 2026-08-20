import os
import sys
import json
import logging
from datetime import datetime, timezone

LOG_FORMAT_JSON = os.environ.get("LOG_FORMAT", "kv").lower() == "json"

RESERVED_LOG_KEYS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName"
}


class StructuredSIEMFormatter(logging.Formatter):
    """
    SIEM-compliant & human-readable structured log formatter.
    Formats logs as Key-Value pairs (or JSON) for SIEM parsers (Datadog, Splunk, Loki, Elastic)
    while remaining clean and readable for human operators.
    """

    def format(self, record):
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3]
        level = record.levelname
        name = record.name
        msg = record.getMessage()

        # Extract extra structured fields attached to the record
        extra_fields = {
            k: v
            for k, v in record.__dict__.items()
            if k not in RESERVED_LOG_KEYS
        }

        if LOG_FORMAT_JSON:
            payload = {
                "timestamp": utc_now,
                "level": level,
                "logger": name,
                "message": msg,
                **extra_fields,
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload)
        else:
            # Key-Value format: 2026-08-20T15:21:26.123Z [INFO] skt-proxy: Message | key1="val1" key2=2
            kv_pairs = " ".join(f"{k}={json.dumps(v)}" for k, v in extra_fields.items())
            base_str = f"{utc_now} [{level:<5}] {name}: {msg}"
            if kv_pairs:
                base_str += f" | {kv_pairs}"
            if record.exc_info:
                base_str += f"\n{self.formatException(record.exc_info)}"
            return base_str


def setup_logger(name: str = "skt-proxy") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredSIEMFormatter())
        logger.addHandler(handler)

    logger.propagate = False
    return logger


def log_siem_event(logger: logging.Logger, level: int, msg: str, event: str, **kwargs):
    """Helper to emit structured SIEM event logs safely without colliding with Python LogRecord attributes."""
    safe_extra = {"event": event}
    for k, v in kwargs.items():
        key_name = f"ctx_{k}" if k in RESERVED_LOG_KEYS else k
        safe_extra[key_name] = v
    logger.log(level, msg, extra=safe_extra)
