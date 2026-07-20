"""Centralized logging setup for the Convointel backend."""

import logging

from backend.app.config import VALID_LOG_LEVELS

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
HANDLER_MARKER = "_convointel_handler"


def configure_logging(log_level: str) -> None:
    normalized_level = log_level.strip().upper()
    if normalized_level not in VALID_LOG_LEVELS:
        allowed = ", ".join(sorted(VALID_LOG_LEVELS))
        raise ValueError(f"CONVOINTEL_LOG_LEVEL must be one of: {allowed}")

    level = logging.getLevelName(normalized_level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    convointel_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, HANDLER_MARKER, False)
    ]

    if not convointel_handlers:
        handler = logging.StreamHandler()
        setattr(handler, HANDLER_MARKER, True)
        root_logger.addHandler(handler)
        convointel_handlers = [handler]

    for handler in convointel_handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
