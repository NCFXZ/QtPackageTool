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
from pathlib import Path


from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QApplication,
    QLabel,
)
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap
from qfluentwidgets import (
    CardWidget,
    TitleLabel,
    StrongBodyLabel,
    CaptionLabel,
    SmoothScrollArea,
    HyperlinkButton,
)
from qfluentwidgets import FluentIcon as FIF

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import UI.ui_layout_settings as ls


class AboutUI(QWidget):
    def __init__(self, version: str, release_date: str):
        """
        Initialize the AboutUI.
        """
        super().__init__()
        self.setObjectName("AboutUI")
        self.setup_ui(version, release_date)

    def setup_ui(self, version: str, release_date: str) -> None:
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

        main_label = TitleLabel("About", self)

        # About
        sys_info_label = StrongBodyLabel("System Information", self)

        sys_info_card_widget = CardWidget()

        sys_info_layout = QHBoxLayout(sys_info_card_widget)

        sys_info_label_layout = QVBoxLayout()

        name_label = StrongBodyLabel("Qt Package Tool", self)
        version_label = CaptionLabel(f"Version: {version}", self)
        release_date_label = CaptionLabel(f"Release Date: {release_date}", self)
        license_label = CaptionLabel(
            "Copyright (c) 2025 NCFXZ<br>"
            'Licensed under the GNU General Public License v3.0 (GPLv3). See <a href="https://github.com/NCFXZ/QtPackageTool/blob/main/LICENSE">LICENSE</a> file for details.<br>'
            'This program uses <a href="https://riverbankcomputing.com/software/pyqt/intro">PyQt6 (GPLv3)</a>.'
        )
        license_label.setOpenExternalLinks(True)

        sys_info_label_layout.addWidget(name_label)
        sys_info_label_layout.addWidget(version_label)
        sys_info_label_layout.addWidget(release_date_label)
        sys_info_label_layout.addWidget(license_label)
        sys_info_label_layout.addStretch(1)

        sys_info_icon = QLabel(self)
        pixmap = QPixmap(self.get_resource_path("resource", "images", "logo.png"))
        pixmap = pixmap.scaled(
            QSize(ls.LARGE_ICON_SIZE, ls.LARGE_ICON_SIZE),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        sys_info_icon.setPixmap(pixmap)

        sys_info_layout.addWidget(sys_info_icon)
        sys_info_layout.addSpacing(ls.SMALL_MARGIN)
        sys_info_layout.addLayout(sys_info_label_layout)
        sys_info_layout.addStretch(1)
        sys_info_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        self.github_hyperlink = HyperlinkButton(
            "https://github.com/NCFXZ/QtPackageTool",
            "GitHub Repository",
            self,
            FIF.GITHUB,
        )
        hyperlink_layout = QHBoxLayout()
        hyperlink_layout.addStretch(1)
        hyperlink_layout.addWidget(self.github_hyperlink)

        # Main Layout
        layout.addWidget(main_label)
        layout.addSpacing(ls.MEDIUM_MARGIN)
        layout.addWidget(sys_info_label)
        layout.addWidget(sys_info_card_widget)
        layout.addLayout(hyperlink_layout)
        layout.addStretch(1)
        layout.setContentsMargins(
            ls.MEDIUM_MARGIN, ls.MEDIUM_MARGIN, ls.MEDIUM_MARGIN, ls.MEDIUM_MARGIN
        )

        scroll_area.setWidget(scroll_widget)
        self.main_layout.addWidget(scroll_area)

        self.setStyleSheet(ls.STYLE_SHEET)

    def get_resource_path(self, *relative_path_parts) -> str:
        """
        Get absolute path to a resource file, works in both source and executable.
        Base path is three levels above the current file (project root).
        """
        if getattr(sys, "frozen", False):
            # Running in a bundled environment
            base_path = Path(sys.executable)
        else:
            # Running in source, go up three levels
            base_path = Path(__file__).resolve().parent.parent.parent
        return str(base_path.joinpath(*relative_path_parts).resolve())


if __name__ == "__main__":
    sys.argv += ["-platform", "windows:darkmode=0"]
    app = QApplication(sys.argv)
    window = AboutUI("UNKNOWN", "UNKNOWN")
    window.show()
    sys.exit(app.exec())
