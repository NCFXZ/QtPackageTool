import os
import sys
from pathlib import Path

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
        self.setWindowIcon(
            QIcon(self.get_resource_path("resource", "images", "logo.png"))
        )
        self.setMinimumSize(1200, 800)
        screen = QApplication.primaryScreen().availableGeometry()
        window_geometry = self.frameGeometry()
        center_point = screen.center()
        window_geometry.moveCenter(center_point)
        self.move(window_geometry.topLeft())

        self.qt_package_settings = QtPackageSettingsUI()
        self.qt_package_project = QtPackageProjectUI()
        self.about_ui = AboutUI("0.7.0", "2025/8/20")

        self.addSubInterface(
            self.qt_package_settings,
            FIF.SETTING,
            "Environment & Project Settings",
            position=NavigationItemPosition.TOP,
            isTransparent=True,
        )
        self.addSubInterface(
            self.qt_package_project,
            FIF.APPLICATION,
            "Package Project",
            position=NavigationItemPosition.TOP,
            isTransparent=True,
        )
        self.addSubInterface(
            self.about_ui,
            FIF.INFO,
            "About",
            position=NavigationItemPosition.BOTTOM,
            isTransparent=True,
        )

        if sys.platform in ["win32"]:
            setThemeColor(getSystemAccentColor(), save=False)
            setTheme(Theme.AUTO)

    def get_resource_path(self, *relative_path_parts) -> str:
        """
        Get absolute path to a resource file, works in both source and executable.
        Base path is three levels above the current file (project root).
        """
        if getattr(sys, "frozen", False):
            # If the application is frozen (packaged), use the executable's directory
            base_path = Path(sys.executable)
        else:
            # If running in source, go up three levels
            base_path = Path(__file__).resolve().parent.parent.parent
        return str(base_path.joinpath(*relative_path_parts).resolve())


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainUI()
    w.show()
    app.exec()
