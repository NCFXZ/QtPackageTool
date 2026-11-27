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

from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QApplication, QLabel, QWidget
from PyQt6.QtCore import QEvent, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    HyperlinkButton,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.metadata import APP_VERSION, RELEASE_DATE, REPOSITORY_URL
import UI.ui_layout_settings as ls
from UI import ui_icons as icons
from UI.i18n import LocalizedPage
from UI.ui_updates import UpdatePanel

LOGO_SIZE = 72


class AboutUI(LocalizedPage):
    update_check_requested = pyqtSignal()
    update_download_requested = pyqtSignal()
    update_cancel_requested = pyqtSignal()
    update_install_requested = pyqtSignal()

    def __init__(self, version: str = APP_VERSION, release_date: str = RELEASE_DATE):
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
        layout = ls.page_content_layout(self)
        layout.addWidget(self.localized(TitleLabel(self), "About"))
        layout.addSpacing(ls.PAGE_MARGIN)
        layout.addWidget(self._overview_card())

        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(self.localized(StrongBodyLabel(self), "Details"))

        version_label = BodyLabel(version, self)
        version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        version_label.setContentsMargins(0, 0, 12, 0)
        release_label = CaptionLabel(self)
        self.bind_text(lambda text: release_label.setText(text.format(date=release_date)), "Release Date: {date}")
        version_card = SimpleCardWidget()
        version_card.setMinimumHeight(ls.FORM_CARD_HEIGHT)
        version_layout = QVBoxLayout(version_card)
        ls.set_card_padding(version_layout)
        self.update_panel = UpdatePanel(version_card, version)
        self.update_check_button = self.update_panel.action_button
        self.update_panel.width_hint_changed.connect(self._sync_action_widths)
        version_row = QWidget(version_card)
        version_row.setFixedHeight(ls.FORM_CARD_HEIGHT - 2 * ls.CARD_VERTICAL_INSET)
        version_row.setLayout(ls.form_row(
            icons.VERSION, self.localized(BodyLabel(self), "Version"), version_label, self.update_check_button,
            caption=release_label,
        ))
        version_layout.addWidget(version_row)
        self.update_panel.check_requested.connect(self.update_check_requested.emit)
        self.update_panel.download_requested.connect(self.update_download_requested.emit)
        self.update_panel.cancel_requested.connect(self.update_cancel_requested.emit)
        self.update_panel.install_requested.connect(self.update_install_requested.emit)
        version_layout.addWidget(self.update_panel)
        layout.addWidget(version_card)

        layout.addWidget(ls.form_card(ls.form_row(
            icons.LICENSE, self.localized(BodyLabel(self), "License"),
            self._link(f"{REPOSITORY_URL}/blob/main/LICENSE", "View License"),
            caption=self.localized(CaptionLabel(self), "GNU General Public License v3.0"),
        )))

        layout.addWidget(ls.form_card(ls.form_row(
            icons.THIRD_PARTY, self.localized(BodyLabel(self), "Third-Party Libraries"),
            self._link("https://riverbankcomputing.com/software/pyqt/intro", "PyQt6"),
            self._link("https://github.com/zhiyiYo/PyQt-Fluent-Widgets", "QFluentWidgets"),
            caption=self.localized(CaptionLabel(self), "Both licensed under GPLv3"),
        )))

        layout.addWidget(ls.form_card(ls.form_row(
            icons.FEEDBACK, self.localized(BodyLabel(self), "Feedback"),
            self._link(f"{REPOSITORY_URL}/issues", "Report Issue"),
            caption=self.localized(CaptionLabel(self), "Report bugs or suggest features"),
        )))
        layout.addStretch(1)
        self._sync_action_widths()

    def _sync_action_widths(self) -> None:
        width = max(ls.action_button_width(self.github_button), self.update_panel.preferred_action_width())
        for button in (self.github_button, self.update_check_button):
            if button.width() != width:
                button.setFixedWidth(width)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._sync_action_widths()

    def set_update_state(self, state: str, version: str = "", notes: str = "", message: str = "") -> None:
        self.update_panel.set_state(state, version, notes, message)

    def set_update_progress(self, received: int, total: int) -> None:
        self.update_panel.set_progress(received, total)

    def set_update_install_allowed(self, allowed: bool) -> None:
        self.update_panel.set_install_allowed(allowed)

    def _overview_card(self) -> SimpleCardWidget:
        card = SimpleCardWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(ls.CARD_INSET, ls.CARD_INSET, ls.CARD_INSET, ls.CARD_INSET)
        row.setSpacing(ls.ICON_TEXT_SPACING)

        logo = QLabel(card)
        # QIcon renders at the device pixel ratio, so the logo stays sharp on high-DPI screens.
        logo.setPixmap(QIcon(icons.app_logo_path()).pixmap(QSize(LOGO_SIZE, LOGO_SIZE)))
        row.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addStretch(1)
        text.addWidget(SubtitleLabel("Qt Package Tool", card))
        copyright_label = CaptionLabel("Copyright (c) 2025 NCFXZ", card)
        copyright_label.setTextColor(light=ls.SECONDARY_TEXT_LIGHT, dark=ls.SECONDARY_TEXT_DARK)
        text.addWidget(copyright_label)
        text.addStretch(1)
        row.addLayout(text, 1)

        self.github_button = self.localized(PushButton(icons.REPOSITORY, "", card), "GitHub Repository")
        self.github_button.setFixedHeight(ls.CONTROL_HEIGHT)
        self.github_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))
        row.addWidget(self.github_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _link(self, url: str, label: str) -> HyperlinkButton:
        button = self.localized(HyperlinkButton(url, "", self), label)
        button.setFixedHeight(ls.CONTROL_HEIGHT)
        button.setToolTip(url)
        return button


if __name__ == "__main__":
    sys.argv += ["-platform", "windows:darkmode=0"]
    app = QApplication(sys.argv)
    window = AboutUI()
    window.show()
    sys.exit(app.exec())
