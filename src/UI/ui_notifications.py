# SPDX-License-Identifier: GPL-3.0-or-later

"""Window messages and Windows notification integration."""

import ctypes

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSystemTrayIcon, QWidget
from qfluentwidgets import InfoBar, InfoBarIcon, InfoBarPosition


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


class WindowNotifications:
    def __init__(self, window: QWidget):
        self.window = window
        self._tray_icon = None

    def info_bar(self, icon: InfoBarIcon, title: str, message: str, duration: int) -> InfoBar:
        bar = InfoBar(
            icon=icon,
            title=title,
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=duration,
            parent=self.window,
        )
        bar.titleLabel.setTextFormat(Qt.TextFormat.PlainText)
        bar.contentLabel.setTextFormat(Qt.TextFormat.PlainText)
        return bar

    def tray_notification(self, title: str, message: str) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self._tray_icon is None:
            self._tray_icon = QSystemTrayIcon(self.window.windowIcon(), parent=self.window)
            self._tray_icon.show()
        self._tray_icon.showMessage(title, message, self._tray_icon.icon(), 5000)

    def highlight_taskbar(self) -> None:
        flash_all = 3
        flash_until_foreground = 12
        flash_info = FLASHWINFO(
            ctypes.sizeof(FLASHWINFO), int(self.window.winId()),
            flash_all | flash_until_foreground, 0, 0,
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(flash_info))
