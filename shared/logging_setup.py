"""Structured logging configuration, shared by every entry point.

Call :func:`setup_logging` once at process start. All modules then use
``logging.getLogger(__name__)`` and inherit this configuration.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int | str = logging.INFO, log_file: Path | None = None) -> None:
    """Configure root logging. Idempotent — safe to call from each CLI command.

    Args:
        level: logging level (int or name such as "DEBUG").
        log_file: optional path; when given, logs are also appended there.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    if isinstance(level, str):
        level = logging.getLevelName(level.upper())

    handlers: list[logging.Handler] = [logging.StreamHandler(stream=sys.stderr)]
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any pre-existing handlers so repeated runs don't duplicate output.
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)

    # edge-tts / google libs are chatty at DEBUG; keep them at WARNING.
    for noisy in ("edge_tts", "googleapiclient", "google_auth_httplib2", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
