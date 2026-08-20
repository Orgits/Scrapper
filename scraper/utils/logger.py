import sys
import json
from loguru import logger
from config.settings import settings


def _json_sink(message):
    """Custom sink for JSON structured logging."""
    record = message.record
    log_entry = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "message": record["message"],
    }
    # Add extra context fields
    for key, value in record["extra"].items():
        log_entry[key] = value
    sys.stdout.write(json.dumps(log_entry) + "\n")


def _text_sink(message):
    """Custom sink for human-readable text logging."""
    record = message.record
    extra_str = ""
    if record["extra"]:
        extra_items = [f"{k}={v}" for k, v in record["extra"].items()]
        extra_str = " | " + " | ".join(extra_items)
    sys.stdout.write(
        f"{record['time']:YYYY-MM-DD HH:mm:ss} | {record['level'].name:<8} | "
        f"{record['message']}{extra_str}\n"
    )


# Remove default handler
logger.remove()

# Add appropriate sink based on format setting
if settings.log_format.lower() == "json":
    logger.add(_json_sink, level=settings.log_level)
else:
    logger.add(_text_sink, level=settings.log_level)

# Also add file sink with rotation
logger.add(
    "logs/scraper_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="14 days",
    level=settings.log_level,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message} | {extra}",
)


def get_logger(name: str = None):
    """Get a logger instance with optional context binding."""
    if name:
        return logger.bind(logger_name=name)
    return logger


class StructuredLogger:
    """Wrapper for structured logging with context."""

    def __init__(self, name: str, **context):
        self._logger = logger.bind(logger_name=name, **context)

    def bind(self, **context):
        return StructuredLogger("", **{**self._logger._context, **context})

    def debug(self, message: str, **kwargs):
        self._logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs):
        self._logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs):
        self._logger.error(message, **kwargs)

    def exception(self, message: str, **kwargs):
        self._logger.exception(message, **kwargs)

    def critical(self, message: str, **kwargs):
        self._logger.critical(message, **kwargs)