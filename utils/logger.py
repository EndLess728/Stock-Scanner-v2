"""Centralized loguru-based logger."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger as log


def setup_logger(
    level: str = "INFO",
    log_file: Optional[str] = "logs/scanner.log",
    rotation: str = "10 MB",
    retention: str = "14 days",
    backtrace: bool = True,
    diagnose: bool = False,
) -> None:
    """Configure global loguru logger.

    - Console sink with colored, structured output.
    - Rotating file sink for persistent logs.
    """
    log.remove()

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    log.add(
        sys.stdout,
        level=level.upper(),
        format=fmt,
        backtrace=backtrace,
        diagnose=diagnose,
        enqueue=True,
    )

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        log.add(
            log_file,
            level=level.upper(),
            format=fmt,
            rotation=rotation,
            retention=retention,
            backtrace=backtrace,
            diagnose=diagnose,
            enqueue=True,
            encoding="utf-8",
        )

    log.info(f"Logger initialized | level={level} file={log_file}")


__all__ = ["log", "setup_logger"]
