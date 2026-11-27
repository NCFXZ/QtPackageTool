# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from qfluentwidgets import FluentIcon as FIF, FluentIconBase, Theme
from qfluentwidgets.common.icon import getIconColor

from app.runtime import RESOURCE_DIR


class _StopIcon(FluentIconBase):
    def icon(self, theme=Theme.AUTO, color=None) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.render(painter, QRectF(0, 0, 32, 32), theme, fill=color or getIconColor(theme))
        painter.end()
        return QIcon(pixmap)

    def render(self, painter, rect, theme=Theme.AUTO, indexes=None, **attributes):
        size = min(rect.width(), rect.height())
        inset = size * 0.17
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(attributes.get("fill", getIconColor(theme))))
        painter.drawRoundedRect(QRectF(rect).adjusted(inset, inset, -inset, -inset), inset / 2, inset / 2)
        painter.restore()


PROJECT_SETUP = FIF.DEVELOPER_TOOLS
PACKAGING = FIF.APPLICATION
SETTINGS = FIF.SETTING
ABOUT = FIF.INFO

QT_KIT = FIF.CODE
BROWSE = FIF.FOLDER
PROJECT = FIF.EDIT
OUTPUT = FIF.SAVE
BUILD_MODE = FIF.COMMAND_PROMPT
CLEAN_BUILD_FILES = FIF.BROOM

EXTERNAL_FILES = FIF.DOCUMENT
ADD_EXTERNAL_FILE = FIF.ADD_TO
ADD_EXTERNAL_FOLDER = FIF.FOLDER_ADD
REMOVE = FIF.REMOVE_FROM
DEPENDENCIES = FIF.LIBRARY
IMPORT_DEPENDENCY = FIF.ADD

START_PACKAGING = FIF.PLAY
STOP_PACKAGING = _StopIcon()
OPEN_PACKAGE_FOLDER = FIF.LINK
COPY_LOG = FIF.COPY
SAVE_LOG = FIF.SAVE_AS
CLEAR_LOG = FIF.DELETE

LANGUAGE = FIF.LANGUAGE
THEME = FIF.PALETTE
PARALLEL_JOBS = FIF.SPEED_HIGH
REPOSITORY = FIF.GITHUB
VERSION = FIF.TAG
LICENSE = FIF.CERTIFICATE
THIRD_PARTY = FIF.HEART
FEEDBACK = FIF.FEEDBACK
AUTO_DOWNLOAD_UPDATES = FIF.CLOUD_DOWNLOAD
UPDATE_CHECK = FIF.SYNC
UPDATE_DOWNLOAD = FIF.DOWNLOAD
UPDATE_INSTALL = FIF.UPDATE
UPDATE_FAILED = FIF.CLOSE


def app_logo_path() -> str:
    return str(RESOURCE_DIR / "images" / "logo.png")
