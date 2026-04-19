"""Structured JSON logger with rotation and module tagging."""

import logging
import sys

from pythonjsonlogger import json as json_log

from app.config import settings


def setup_logger(name: str = "tweetveet") -> logging.Logger:
    """Create and configure a structured JSON logger.

    Args:
        name: Logger name, typically the module name.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # Console handler with JSON formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    formatter = json_log.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


# Default application logger
logger = setup_logger()
