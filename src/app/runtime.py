# SPDX-License-Identifier: GPL-3.0-or-later

import __main__
import os
import sys
from pathlib import Path

from loguru import logger


IS_COMPILED = bool(getattr(__main__, "__compiled__", False))
APP_DIR = Path(__file__).parents[1] if IS_COMPILED else Path(__file__).parents[2]
RESOURCE_DIR = APP_DIR / "resource"


def configure_logging() -> None:
    logger.remove()
    options = {
        "level": "INFO",
        "format": "{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}.{function}:{line} - {message}",
        "diagnose": False,
    }
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            try:
                logger.add(stream, **options)
                break
            except (OSError, TypeError, ValueError):
                continue

    log_dirs = [] if IS_COMPILED else [APP_DIR / "logs"]
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data and Path(local_data).is_absolute():
        log_dirs.append(Path(local_data) / "QtPackageTool" / "logs")
    for directory in log_dirs:
        try:
            log_path = str(directory / "app.log").replace("{", "{{").replace("}", "}}")
            logger.add(log_path, rotation="10 MB", retention="7 days", **options)
            return
        except (OSError, ValueError) as exc:
            logger.warning(f"Could not open log directory {directory}: {exc}")


def exception_handler(exc_type, exc_value, exc_traceback) -> None:
    logger.opt(exception=(exc_type, exc_value, exc_traceback)).error("Unhandled exception")
