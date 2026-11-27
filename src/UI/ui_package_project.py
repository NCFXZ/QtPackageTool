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

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QApplication,
)
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ProgressBar,
    PushButton,
    SimpleCardWidget,
    TitleLabel,
    StrongBodyLabel,
    TextBrowser,
    ToolTipFilter,
    TransparentToolButton,
    VerticalSeparator,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import UI.ui_layout_settings as ls
from UI import ui_icons as icons
from UI.i18n import LocalizedPage, tr


SUMMARY_FIELDS = (
    ("project", "Project"),
    ("kit", "Qt Kit"),
    ("mode", "Build Mode"),
    ("output", "Output Path"),
)


class QtPackageProjectUI(LocalizedPage):
    notify = pyqtSignal(int, str)

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
        layout = ls.page_content_layout(self)

        main_label = self.localized(TitleLabel(self), "Packaging")

        # Summary
        summary_label = self.localized(StrongBodyLabel(self), "Build Summary")
        summary_card = SimpleCardWidget()
        summary_card.setFixedHeight(ls.FORM_CARD_HEIGHT)
        summary_layout = QHBoxLayout(summary_card)
        summary_layout.setContentsMargins(ls.CARD_INSET, 0, ls.CARD_INSET, 0)
        summary_layout.setSpacing(ls.ICON_TEXT_SPACING)
        self._summary = {key: "" for key, _ in SUMMARY_FIELDS}
        self._summary_labels = {}
        for key, title in SUMMARY_FIELDS:
            column = QVBoxLayout()
            column.setSpacing(2)
            column.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            caption = self.localized(CaptionLabel(summary_card), title)
            caption.setTextColor(light=ls.SECONDARY_TEXT_LIGHT, dark=ls.SECONDARY_TEXT_DARK)
            value = ls.ElidedLabel(summary_card)
            column.addWidget(caption)
            column.addWidget(value)
            summary_layout.addLayout(column, 1)
            self._summary_labels[key] = value
        self._refresh_summary()

        # Package
        package_label = self.localized(StrongBodyLabel(self), "Packaging Progress")
        package_card_widget = SimpleCardWidget()
        package_layout = QVBoxLayout(package_card_widget)
        ls.set_card_padding(package_layout)

        self.progress_status_label = BodyLabel(package_card_widget)
        self.progress_percent_label = BodyLabel(package_card_widget)
        self.progress_percent_label.setTextColor(light=ls.SECONDARY_TEXT_LIGHT, dark=ls.SECONDARY_TEXT_DARK)
        self.package_progress_bar = ProgressBar(package_card_widget)
        self.package_progress_bar.setRange(0, 0)
        self.set_packaging_progress("Waiting to start")
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_status_label)
        progress_row.addStretch(1)
        progress_row.addWidget(self.progress_percent_label)
        progress_column = QVBoxLayout()
        progress_column.setSpacing(ls.PAGE_SPACING)
        progress_column.addLayout(progress_row)
        progress_column.addWidget(self.package_progress_bar)

        # Progress on the left, log actions on the right of a divider
        header_row = QHBoxLayout()
        header_row.setSpacing(0)
        header_row.addLayout(progress_column, 1)
        header_row.addSpacing(ls.ICON_TEXT_SPACING)
        header_row.addWidget(VerticalSeparator(package_card_widget))
        header_row.addSpacing(ls.CONTROL_SPACING)
        self._log_actions = []
        for icon, tip, action in (
            (icons.COPY_LOG, "Copy Log", self.copy_log),
            (icons.SAVE_LOG, "Save Log", self.save_log),
            (icons.CLEAR_LOG, "Clear Log", self.clear_log),
        ):
            button = TransparentToolButton(icon, package_card_widget)
            button.setFixedSize(ls.CONTROL_HEIGHT, ls.CONTROL_HEIGHT)
            button.installEventFilter(ToolTipFilter(button))
            self.bind_text(button.setToolTip, tip)
            button.clicked.connect(action)
            header_row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self._log_actions.append(button)

        self.package_terminal = TextBrowser(self)
        self.package_terminal.document().setMaximumBlockCount(2000)
        self.package_terminal.setFont(QFont("Consolas", 10))
        self.package_terminal.setMinimumHeight(240)
        self.bind_text(self.package_terminal.setPlaceholderText, "Build output appears here")
        self.package_terminal.textChanged.connect(self._update_log_actions)
        self._update_log_actions()

        self.package_toggle_button = PushButton(self)
        self.set_packaging_action("Start Packaging")
        self.package_folder_button = self.localized(
            PushButton(icons.OPEN_PACKAGE_FOLDER, "", self), "Open Package Folder"
        )
        self.package_folder_button.setDisabled(True)
        self._sync_action_widths()

        package_layout.addLayout(header_row)
        package_layout.addWidget(self.package_terminal, 1)
        package_layout.addLayout(ls.centered_actions(self.package_toggle_button, self.package_folder_button))

        # Main Layout
        layout.addWidget(main_label)
        layout.addSpacing(ls.PAGE_MARGIN)
        layout.addWidget(summary_label)
        layout.addWidget(summary_card)
        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(package_label)
        layout.addWidget(package_card_widget, 1)

    def set_build_summary(self, **values: str) -> None:
        self._summary.update(values)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        for key, label in self._summary_labels.items():
            label.setText(self._summary[key] or tr("Not selected"))

    def _update_log_actions(self) -> None:
        has_log = not self.package_terminal.document().isEmpty()
        for button in self._log_actions:
            button.setEnabled(has_log)

    def copy_log(self) -> None:
        QApplication.clipboard().setText(self.package_terminal.toPlainText())
        self.notify.emit(1, tr("Log copied to the clipboard."))

    def save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save Log"), "build.log", tr("Log Files (*.log *.txt)")
        )
        if not path:
            return
        try:
            Path(path).write_text(self.package_terminal.toPlainText() + "\n", encoding="utf-8")
        except OSError:
            self.notify.emit(0, tr("Could not save the log."))
            return
        self.notify.emit(1, tr("Log saved."))

    def clear_log(self) -> None:
        self.package_terminal.clear()

    def set_packaging_action(self, source: str) -> None:
        self._packaging_action = source
        self.package_toggle_button.setText(tr(source))
        icon = icons.START_PACKAGING if source == "Start Packaging" else icons.STOP_PACKAGING
        self.package_toggle_button.setIcon(icon)
        if hasattr(self, "package_folder_button"):
            self._sync_action_widths()

    def _sync_action_widths(self) -> None:
        actions = (tr("Start Packaging"), tr("Stop Packaging"), tr("Stopping..."))
        width = max(
            ls.action_button_width(self.package_toggle_button, actions),
            ls.action_button_width(self.package_folder_button),
        )
        for button in (self.package_toggle_button, self.package_folder_button):
            button.setFixedSize(width, ls.CONTROL_HEIGHT)

    def set_packaging_progress(
        self, status: str, counts: tuple[int, int] | None = None, *, failed: bool = False
    ) -> None:
        self._progress_status = status
        if counts is not None:
            completed, total = counts
            self.package_progress_bar.setRange(0, total)
            self.package_progress_bar.setValue(completed)
        self.package_progress_bar.setError(failed)
        self._refresh_progress_text()

    def _refresh_progress_text(self) -> None:
        status = tr(self._progress_status)
        self.progress_status_label.setText(status)
        self.package_progress_bar.setAccessibleName(status)
        self.progress_percent_label.setText(self.package_progress_bar.text())

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.set_packaging_action(self._packaging_action)
            self._refresh_progress_text()
            self._refresh_summary()

    def append_terminal(self, text: str, color: str | None = None) -> None:
        cursor = self.package_terminal.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.package_terminal.document().isEmpty():
            cursor.insertBlock()
        style = QTextCharFormat()
        if color:
            style.setForeground(QColor(color))
            style.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(text.rstrip("\r\n"), style)
        self.package_terminal.setTextCursor(cursor)
        self.package_terminal.ensureCursorVisible()


if __name__ == "__main__":
    sys.argv += ["-platform", "windows:darkmode=0"]
    app = QApplication(sys.argv)
    window = QtPackageProjectUI()
    window.show()
    sys.exit(app.exec())
