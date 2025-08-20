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
from PyQt6.QtCore import QSize
from qfluentwidgets import (
    CardWidget,
    ComboBox,
    PushButton,
    LineEdit,
    TransparentToolButton,
    TitleLabel,
    StrongBodyLabel,
    BodyLabel,
    CaptionLabel,
    SwitchButton,
    SmoothScrollArea,
    HyperlinkButton,
)
from qfluentwidgets import FluentIcon as FIF

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import UI.ui_layout_settings as ls


class QtPackageSettingsUI(QWidget):
    def __init__(self):
        """
        Initialize the QtPackageSettingsUI.
        """
        super().__init__()
        self.setObjectName("QtPackageSettingsUI")
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

        main_label = TitleLabel("Environment & Project Settings", self)

        # Environment
        env_label = StrongBodyLabel("Environment", self)
        env_card_widget = CardWidget()
        env_layout = QHBoxLayout(env_card_widget)

        env_label_group_layout = QVBoxLayout()

        env_title = BodyLabel("Qt Environment Configuration", self)
        env_description = CaptionLabel(
            "Select the Qt version and compiler to build your project", self
        )
        env_label_group_layout.addWidget(env_title)
        env_label_group_layout.addWidget(env_description)

        env_qt_version_label = BodyLabel("Qt Version:", self)
        self.env_qt_version_combo_box = ComboBox(self)

        env_qt_mingw_label = BodyLabel("MinGW:", self)
        self.env_qt_mingw_combo_box = ComboBox(self)

        self.env_button = PushButton(FIF.FOLDER, "Browse...", self)

        env_icon = TransparentToolButton(FIF.TRANSPARENT, self)
        env_icon.setFixedSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE)
        env_icon.setIconSize(QSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE))

        env_layout.addWidget(env_icon)
        env_layout.addSpacing(ls.SMALL_MARGIN)
        env_layout.addLayout(env_label_group_layout)
        env_layout.addStretch(1)
        env_layout.addWidget(env_qt_version_label)
        env_layout.addWidget(self.env_qt_version_combo_box)
        env_layout.addSpacing(ls.SMALL_MARGIN)
        env_layout.addWidget(env_qt_mingw_label)
        env_layout.addWidget(self.env_qt_mingw_combo_box)
        env_layout.addSpacing(ls.SMALL_MARGIN)
        env_layout.addWidget(self.env_button)

        env_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        self.env_compiler_mingw_version_hyperlink = HyperlinkButton(
            "https://wiki.qt.io/MinGW",
            "Find the MinGW compiler corresponding to the Qt version",
            self,
            FIF.LINK,
        )
        hyperlink_layout = QHBoxLayout()
        hyperlink_layout.addStretch(1)
        hyperlink_layout.addWidget(self.env_compiler_mingw_version_hyperlink)

        # Project Selection
        project_label = StrongBodyLabel("Project", self)
        project_select_card_widget = CardWidget()
        project_layout = QHBoxLayout(project_select_card_widget)

        project_select_label_group_layout = QVBoxLayout()

        project_select_title = BodyLabel("Project Selection", self)
        project_select_description = CaptionLabel(
            "Select a Qt project (.pro) file to configure the project", self
        )

        self.project_select_edit = LineEdit(self)
        self.project_select_edit.setPlaceholderText(
            "Choose a Qt project (.pro) file..."
        )
        self.project_select_edit.setReadOnly(True)
        self.project_select_edit.setMinimumWidth(ls.LARGE_EDIT_WIDTH)

        project_select_label_group_layout.addWidget(project_select_title)
        project_select_label_group_layout.addWidget(project_select_description)

        project_select_icon = TransparentToolButton(FIF.DOCUMENT, self)
        project_select_icon.setFixedSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE)
        project_select_icon.setIconSize(QSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE))

        self.project_button = PushButton(FIF.FOLDER, "Browse...", self)

        project_layout.addWidget(project_select_icon)
        project_layout.addSpacing(ls.SMALL_MARGIN)
        project_layout.addLayout(project_select_label_group_layout)
        project_layout.addStretch(1)
        project_layout.addWidget(self.project_select_edit)
        project_layout.addSpacing(ls.SMALL_MARGIN)
        project_layout.addWidget(self.project_button)

        project_select_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        # Build Options
        build_card_widget = CardWidget()
        build_layout = QHBoxLayout(build_card_widget)

        build_label_group_layout = QVBoxLayout()

        build_title = BodyLabel("Build Options", self)
        build_description = CaptionLabel(
            "Select the build configuration for your project", self
        )
        build_label_group_layout.addWidget(build_title)
        build_label_group_layout.addWidget(build_description)

        self.build_combo_box = ComboBox(self)
        self.build_combo_box.addItems(["Release", "Debug"])

        build_icon = TransparentToolButton(FIF.COMMAND_PROMPT, self)
        build_icon.setFixedSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE)
        build_icon.setIconSize(QSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE))

        build_layout.addWidget(build_icon)
        build_layout.addSpacing(ls.SMALL_MARGIN)
        build_layout.addLayout(build_label_group_layout)
        build_layout.addStretch(1)
        build_layout.addWidget(self.build_combo_box)

        build_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        # Output Path
        output_path_card_widget = CardWidget()
        output_path_layout = QHBoxLayout(output_path_card_widget)

        output_path_label_group_layout = QVBoxLayout()

        output_path_title = BodyLabel("Output Path", self)
        output_path_description = CaptionLabel(
            "Select the folder where the compiled application and deployment files will be saved",
            self,
        )
        output_path_label_group_layout.addWidget(output_path_title)
        output_path_label_group_layout.addWidget(output_path_description)

        self.output_path_edit = LineEdit(self)
        self.output_path_edit.setPlaceholderText("Choose an output folder...")
        self.output_path_edit.setReadOnly(True)
        self.output_path_edit.setMinimumWidth(ls.LARGE_EDIT_WIDTH)

        output_path_icon = TransparentToolButton(FIF.SAVE, self)
        output_path_icon.setFixedSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE)
        output_path_icon.setIconSize(QSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE))

        self.output_path_button = PushButton(FIF.FOLDER, "Browse...", self)

        output_path_layout.addWidget(output_path_icon)
        output_path_layout.addSpacing(ls.SMALL_MARGIN)
        output_path_layout.addLayout(output_path_label_group_layout)
        output_path_layout.addStretch(1)
        output_path_layout.addWidget(self.output_path_edit)
        output_path_layout.addSpacing(ls.SMALL_MARGIN)
        output_path_layout.addWidget(self.output_path_button)

        output_path_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        # Auto-Clean Build Files
        clean_card_widget = CardWidget()
        clean_layout = QHBoxLayout(clean_card_widget)

        clean_label_group_layout = QVBoxLayout()

        clean_title = BodyLabel("Auto-Clean Build Files", self)
        clean_description = CaptionLabel(
            "Remove all .cpp, .c, and .o files from the build folder after building",
            self,
        )
        clean_label_group_layout.addWidget(clean_title)
        clean_label_group_layout.addWidget(clean_description)

        self.clean_switch = SwitchButton(self)

        clean_icon = TransparentToolButton(FIF.CODE, self)
        clean_icon.setFixedSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE)
        clean_icon.setIconSize(QSize(ls.SMALL_ICON_SIZE, ls.SMALL_ICON_SIZE))

        clean_layout.addWidget(clean_icon)
        clean_layout.addSpacing(ls.SMALL_MARGIN)
        clean_layout.addLayout(clean_label_group_layout)
        clean_layout.addStretch(1)
        clean_layout.addWidget(self.clean_switch)

        clean_card_widget.setContentsMargins(
            ls.SMALL_MARGIN, ls.NANO_MARGIN, ls.SMALL_MARGIN, ls.NANO_MARGIN
        )

        # Main Layout
        layout.addWidget(main_label)
        layout.addSpacing(ls.MEDIUM_MARGIN)
        layout.addWidget(env_label)
        layout.addWidget(env_card_widget)
        layout.addLayout(hyperlink_layout)
        layout.addSpacing(ls.SMALL_MARGIN)
        layout.addWidget(project_label)
        layout.addWidget(project_select_card_widget)
        layout.addWidget(output_path_card_widget)
        layout.addWidget(build_card_widget)
        layout.addWidget(clean_card_widget)
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
    window = QtPackageSettingsUI()
    window.show()
    sys.exit(app.exec())
