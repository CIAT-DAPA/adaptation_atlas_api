"""Logging configuration for the API."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger with a consistent format and level.

    Args:
        level: Logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    numeric_level = logging.getLevelName(level.upper())

    # ``getLevelName`` returns a string when the name is unknown; fall back to INFO.
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    logging.basicConfig(
        level=numeric_level,
        format=LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )

    # Third-party libraries are noisy at DEBUG level; keep them at INFO or above.
    for noisy_logger in ("botocore", "boto3", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy_logger).setLevel(max(numeric_level, logging.INFO))
