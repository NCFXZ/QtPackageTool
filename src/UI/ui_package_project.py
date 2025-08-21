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

import sys
import os

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QApplication,
)
from PyQt6.QtGui import QFont
from qfluentwidgets import (
    CardWidget,
    PushButton,
    TitleLabel,
    StrongBodyLabel,
    SmoothScrollArea,
    TextBrowser,
)
from qfluentwidgets import FluentIcon as FIF

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import UI.ui_layout_settings as ls


class QtPackageProjectUI(QWidget):
    def __init__(self):
        """
        Initialize the QtPackageProjectUI.
        """
        super().__init__()
        self.setObjectName("QtPackageProjectUI")
        self.setup_ui()

    def setup_ui(self) -> None:
        """
        Setup the UI components.
        """
        self.main_layout = QVBoxLayout(self)
        scroll_area = SmoothScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.enableTransparentBackground()
        scroll_widget = QWidget()
        scroll_widget.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(scroll_widget)

        main_label = TitleLabel("Package Project", self)

        # Package
        package_label = StrongBodyLabel("Package", self)
        package_card_widget = CardWidget()
        package_layout = QVBoxLayout(package_card_widget)

        self.package_terminal = TextBrowser(self)
        self.package_terminal.setFont(QFont("Consolas", 10))
        self.package_terminal.setMinimumHeight(400)
        self.package_terminal.setText(
            "Terminal Outputs...\nClick the button to start packaging..."
        )

        self.package_toggle_button = PushButton(FIF.PLAY, "Start Packaging", self)
        self.package_folder_button = PushButton(FIF.FOLDER, "Open Package Folder", self)
        self.package_folder_button.setDisabled(True)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.package_toggle_button)
        button_layout.addSpacing(ls.SMALL_MARGIN)
        button_layout.addWidget(self.package_folder_button)
        button_layout.addStretch(1)

        package_layout.addWidget(self.package_terminal)
        package_layout.addSpacing(ls.SMALL_MARGIN)
        package_layout.addLayout(button_layout)

        package_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        # Main Layout
        layout.addWidget(main_label)
        layout.addSpacing(ls.MEDIUM_MARGIN)
        layout.addWidget(package_label)
        layout.addWidget(package_card_widget)
        layout.addStretch(1)
        layout.setContentsMargins(
            ls.MEDIUM_MARGIN, ls.MEDIUM_MARGIN, ls.MEDIUM_MARGIN, ls.MEDIUM_MARGIN
        )

        scroll_area.setWidget(scroll_widget)
        self.main_layout.addWidget(scroll_area)

        self.setStyleSheet(ls.STYLE_SHEET)


if __name__ == "__main__":
    sys.argv += ["-platform", "windows:darkmode=0"]
    app = QApplication(sys.argv)
    window = QtPackageProjectUI()
    window.show()
    sys.exit(app.exec())
