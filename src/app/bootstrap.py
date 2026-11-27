# SPDX-License-Identifier: GPL-3.0-or-later

"""Initialize the application and enter its Qt event loop."""

import sys

from loguru import logger
import win32event
import pywintypes

from .metadata import APP_NAME, APP_VERSION, APPLICATION_MUTEX, UPDATE_MUTEX
from .runtime import RESOURCE_DIR, configure_logging, configure_windows_identity, exception_handler


def run() -> int:
    configure_logging()
    sys.excepthook = exception_handler
    configure_windows_identity()
    # Keep the mutex alive until the process exits so the installer can detect running instances.
    application_mutex = win32event.CreateMutex(None, False, APPLICATION_MUTEX)
    try:
        update_mutex = win32event.OpenMutex(0x00100000, False, UPDATE_MUTEX)
    except pywintypes.error as error:
        if error.winerror != 2:
            raise
    else:
        update_mutex.Close()
        application_mutex.Close()
        return 0
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon

    app = QApplication(sys.argv)
    app._application_mutex = application_mutex
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(RESOURCE_DIR / "images" / "logo.ico")))
    from .controller import QtPackage

    w = QtPackage()
    w.ui.show()
    result = app.exec()
    logger.info("Program exited")
    return result
