# SPDX-License-Identifier: GPL-3.0-or-later

import __main__
import os
import sys
from pathlib import Path

from loguru import logger

from .metadata import APP_USER_MODEL_ID


IS_COMPILED = bool(getattr(__main__, "__compiled__", False))
APP_DIR = Path(__file__).parents[1] if IS_COMPILED else Path(__file__).parents[2]
RESOURCE_DIR = APP_DIR / "resource"


def configure_windows_identity() -> None:
    """Give source and packaged windows the same stable taskbar identity as shortcuts."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        set_app_id = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = (ctypes.c_wchar_p,)
        set_app_id.restype = ctypes.c_long
        result = set_app_id(APP_USER_MODEL_ID)
        if result != 0:
            logger.warning("Could not set the taskbar application identity: HRESULT {:#x}", result & 0xffffffff)
    except OSError as error:
        logger.warning("Could not set the taskbar application identity: {}", error)


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
