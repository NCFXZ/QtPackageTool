# Qt Package Tool - A PyQt6-based application
# Copyright (c) 2025 NCFXZ
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import sys

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import (
    DotInfoBadge,
    InfoBadgePosition,
    NavigationItemPosition,
    setTheme,
    setThemeColor,
    FluentWindow,
)
from qframelesswindow.utils import getSystemAccentColor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.runtime import RESOURCE_DIR
from app.preferences import AppPreferences
from app.metadata import APP_VERSION, RELEASE_DATE
from UI import ui_icons as icons
from UI.ui_package_settings import QtPackageSettingsUI
from UI.ui_package_project import QtPackageProjectUI
from UI.ui_about import AboutUI
from UI.i18n import tr
from UI.ui_preferences import PreferencesUI, THEMES


class MainUI(FluentWindow):
    def __init__(self, preferences: AppPreferences | None = None):
        """
        Initialize the MainUI.
        """
        super().__init__()
        self.setObjectName("MainUI")
        self._navigation_labels = ()
        self._packaging_badge = None
        self._packaging_badge_level = None
        self.preferences = preferences if preferences is not None else AppPreferences(RESOURCE_DIR)
        self.preferences.theme_changed.connect(self.apply_theme)
        QApplication.styleHints().colorSchemeChanged.connect(self.system_theme_changed)
        if sys.platform == "win32":
            setThemeColor(getSystemAccentColor(), save=False)
        self.apply_theme(self.preferences.theme)
        self.setup_ui()

    def setup_ui(self) -> None:
        """
        Setup the UI components.
        """

        self.setWindowTitle("Qt Package Tool")
        self.setWindowIcon(QIcon(icons.app_logo_path()))
        self.setMinimumSize(1180, 680)
        primary = QApplication.primaryScreen()
        if primary is not None:
            screen = primary.availableGeometry()
            self.resize(self.minimumWidth(), max(self.minimumHeight(), min(800, screen.height())))
            window_geometry = self.frameGeometry()
            window_geometry.moveCenter(screen.center())
            self.move(window_geometry.topLeft())
        else:
            self.resize(self.minimumWidth(), 800)
        self.restoreGeometry(self.preferences.window_geometry())

        self.qt_package_settings = QtPackageSettingsUI()
        self.qt_package_project = QtPackageProjectUI()
        self.preferences_ui = PreferencesUI(self.preferences)
        self.about_ui = AboutUI(APP_VERSION, RELEASE_DATE)

        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setMinimumExpandWidth(self.minimumWidth())
        self.navigationInterface.setMenuButtonVisible(False)
        self._navigation_labels = (
            (self.qt_package_settings, icons.PROJECT_SETUP, "Project Setup", NavigationItemPosition.TOP),
            (self.qt_package_project, icons.PACKAGING, "Packaging", NavigationItemPosition.TOP),
            (self.preferences_ui, icons.SETTINGS, "Settings", NavigationItemPosition.BOTTOM),
            (self.about_ui, icons.ABOUT, "About", NavigationItemPosition.BOTTOM),
        )
        for page, icon, label, position in self._navigation_labels:
            self.addSubInterface(page, icon, tr(label), position=position, isTransparent=True)
        self.navigationInterface.expand(useAni=False)
        self.stackedWidget.currentChanged.connect(self._clear_viewed_badge)

    def set_packaging_badge(self, level: str | None) -> None:
        """
        Mark the packaging item: "attension" while building, "success" or "error" until it is viewed.
        """
        if self._packaging_badge is not None:
            self._packaging_badge.deleteLater()
            self._packaging_badge = None
        self._packaging_badge_level = None
        viewing = self.stackedWidget.currentWidget() is self.qt_package_project
        if level is None or (viewing and level != "attension"):
            return
        item = self.navigationInterface.widget(self.qt_package_project.objectName())
        self._packaging_badge = getattr(DotInfoBadge, level)(item.parent(), item, InfoBadgePosition.NAVIGATION_ITEM)
        self._packaging_badge.show()
        self._packaging_badge_level = level

    def _clear_viewed_badge(self) -> None:
        viewing = self.stackedWidget.currentWidget() is self.qt_package_project
        if viewing and self._packaging_badge_level not in (None, "attension"):
            self.set_packaging_badge(None)

    def closeEvent(self, event) -> None:
        self.preferences.save_window_geometry(self.saveGeometry())
        super().closeEvent(event)

    def apply_theme(self, theme: str, scheme: Qt.ColorScheme | None = None) -> None:
        if theme == "system":
            if scheme is None:
                scheme = QApplication.styleHints().colorScheme()
            if scheme != Qt.ColorScheme.Unknown:
                theme = "dark" if scheme == Qt.ColorScheme.Dark else "light"
        setTheme(THEMES[theme], save=False)

    def system_theme_changed(self, scheme: Qt.ColorScheme) -> None:
        if self.preferences.theme == "system":
            self.apply_theme("system", scheme)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            for page, _, label, _ in self._navigation_labels:
                item = self.navigationInterface.widget(page.objectName())
                item.setText(tr(label))
                item.setToolTip(tr(label))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainUI()
    w.show()
    app.exec()
