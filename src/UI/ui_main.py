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

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import (
    NavigationItemPosition,
    setTheme,
    setThemeColor,
    Theme,
    FluentWindow,
)
from qfluentwidgets import FluentIcon as FIF
from qframelesswindow.utils import getSystemAccentColor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from UI.ui_package_settings import QtPackageSettingsUI
from UI.ui_package_project import QtPackageProjectUI
from UI.ui_about import AboutUI


class MainUI(FluentWindow):
    def __init__(self):
        """
        Initialize the MainUI.
        """
        super().__init__()
        self.setObjectName("MainUI")
        self.setup_ui()

    def setup_ui(self) -> None:
        """
        Setup the UI components.
        """

        self.setWindowTitle("Qt Package Tool")
        relative_logo_path = os.path.join("..", "resource", "images", "logo.png")
        logo_path = os.path.join(os.path.dirname(__file__), relative_logo_path)
        self.setWindowIcon(QIcon(logo_path))
        self.setMinimumSize(1200, 800)
        screen = QApplication.primaryScreen().availableGeometry()
        window_geometry = self.frameGeometry()
        center_point = screen.center()
        window_geometry.moveCenter(center_point)
        self.move(window_geometry.topLeft())

        self.qt_package_settings = QtPackageSettingsUI()
        self.qt_package_project = QtPackageProjectUI()
        self.about_ui = AboutUI("0.7.1 Beta", "2025/8/21")

        self.addSubInterface(
            self.qt_package_settings,
            FIF.SETTING,
            "Environment & Project Settings",
            position=NavigationItemPosition.TOP,
            isTransparent=False,
        )
        self.addSubInterface(
            self.qt_package_project,
            FIF.APPLICATION,
            "Package Project",
            position=NavigationItemPosition.TOP,
            isTransparent=False,
        )
        self.addSubInterface(
            self.about_ui,
            FIF.INFO,
            "About",
            position=NavigationItemPosition.BOTTOM,
            isTransparent=False,
        )

        if sys.platform in ["win32"]:
            setThemeColor(getSystemAccentColor(), save=False)
            setTheme(Theme.AUTO)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainUI()
    w.show()
    app.exec()
