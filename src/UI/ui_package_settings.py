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
    QVBoxLayout,
    QHBoxLayout,
    QApplication,
    QCompleter,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)
from PyQt6.QtCore import QEvent, QMimeData, QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from qfluentwidgets import (
    ComboBox,
    EditableComboBox,
    PushButton,
    LineEdit,
    SimpleCardWidget,
    SmoothScrollArea,
    FluentIconBase,
    TitleLabel,
    StrongBodyLabel,
    BodyLabel,
    CaptionLabel,
    SwitchButton,
    TableWidget,
    isDarkTheme,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import UI.ui_layout_settings as ls
from UI import ui_icons as icons
from UI.i18n import LocalizedPage, tr


def local_paths(mime: QMimeData) -> list[str]:
    urls = mime.urls() if mime.hasUrls() else []
    paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
    return paths if paths and len(paths) == len(urls) else []


def project_file(mime: QMimeData) -> str:
    """Return the dropped .pro or CMakeLists.txt file, if exactly one was dropped."""
    paths = local_paths(mime)
    if len(paths) != 1:
        return ""
    name = paths[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return paths[0] if name.endswith(".pro") or name == "cmakelists.txt" else ""


def table_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setToolTip(text)
    return item


class PathTable(TableWidget):
    """
    Table that shows a hint while empty and can accept dropped local files and folders.
    """

    paths_dropped = pyqtSignal(list)

    def __init__(self, parent: QWidget, accept_drops: bool = False):
        super().__init__(parent)
        self._placeholder = ""
        self.setAcceptDrops(accept_drops)
        self.viewport().setAcceptDrops(accept_drops)
        model = self.model()
        model.rowsInserted.connect(self.viewport().update)
        model.rowsRemoved.connect(self.viewport().update)

    def setPlaceholderText(self, text: str) -> None:
        self._placeholder = text
        self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.rowCount() or not self._placeholder:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor(ls.SECONDARY_TEXT_DARK if isDarkTheme() else ls.SECONDARY_TEXT_LIGHT))
        painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)

    def dragEnterEvent(self, event) -> None:
        if local_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = local_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.paths_dropped.emit(paths)


class QtPackageSettingsUI(LocalizedPage):
    project_dropped = pyqtSignal(str)

    def __init__(self):
        """
        Initialize the QtPackageSettingsUI.
        """
        super().__init__()
        self.setObjectName("QtPackageSettingsUI")
        self._kit_scanning = False
        self._kit_placeholder = "Select a Qt kit"
        self._dependency_placeholder = "Select a Qt kit first"
        self.setup_ui()
        self.setAcceptDrops(True)

    def setup_ui(self) -> None:
        """
        Setup the UI components.
        """
        layout = ls.page_content_layout(self)
        layout.addWidget(self.localized(TitleLabel(self), "Project Setup"))
        layout.addSpacing(ls.PAGE_MARGIN)

        layout.addWidget(self.localized(StrongBodyLabel(self), "Environment"))
        self.kit_combo = ComboBox(self)
        self.kit_combo.setFixedSize(ls.FORM_FIELD_WIDTH, ls.CONTROL_HEIGHT)
        self.env_button = self._action(icons.BROWSE, "Browse...")
        self.set_kit_search_state(False)
        layout.addWidget(ls.form_card(self._form_row(
            icons.QT_KIT, "Qt Kit", self.kit_combo, self.env_button,
            caption="MinGW or MSVC is matched automatically",
        )))

        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(self.localized(StrongBodyLabel(self), "Project"))

        self.project_select_edit = self._path_edit("Select a .pro or CMakeLists.txt file")
        self.project_button = self._action(icons.BROWSE, "Browse...")
        layout.addWidget(ls.form_card(self._form_row(
            icons.PROJECT, "Project Selection", self.project_select_edit, self.project_button,
            caption="Only build trusted qmake or CMake projects",
        )))

        self.output_path_edit = self._path_edit("Select an output folder")
        self.output_path_button = self._action(icons.BROWSE, "Browse...")
        layout.addWidget(ls.form_card(self._form_row(
            icons.OUTPUT, "Output Path", self.output_path_edit, self.output_path_button,
            caption="One subfolder per package",
        )))

        self.build_combo_box = ComboBox(self)
        self.build_combo_box.setFixedSize(ls.ACTION_WIDTH, ls.CONTROL_HEIGHT)
        self.build_combo_box.addItem("Release", userData=True)
        self.build_combo_box.addItem("Debug", userData=False)
        layout.addWidget(ls.form_card(self._form_row(
            icons.BUILD_MODE, "Build Options", self.build_combo_box
        )))

        self.clean_switch = SwitchButton(self)
        self.bind_text(self.clean_switch.setOnText, "On")
        self.bind_text(self.clean_switch.setOffText, "Off")
        layout.addWidget(ls.form_card(self._form_row(
            icons.CLEAN_BUILD_FILES, "Auto-Clean Build Files", self.clean_switch,
            caption="Only this build's temporary files",
        )))

        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(self.localized(StrongBodyLabel(self), "External Files & Dependencies"))

        self.external_table = self._table(2, accept_drops=True)
        self.bind_text(self.external_table.setPlaceholderText, "Drop files or folders here, or use the buttons below")
        self.external_file_button = self._action(icons.ADD_EXTERNAL_FILE, "Select File")
        self.external_folder_button = self._action(icons.ADD_EXTERNAL_FOLDER, "Select Folder")
        self.external_remove_button = self._action(icons.REMOVE, "Remove")
        self.external_remove_button.clicked.connect(lambda: self.remove_selected_rows(self.external_table))
        layout.addWidget(self._table_card(
            self._form_row(icons.EXTERNAL_FILES, "Include External Files / Folders"),
            self.external_table,
            self.external_file_button, self.external_folder_button, self.external_remove_button,
        ))

        self.dependencies_module_combo_box = EditableComboBox(self)
        self.dependencies_module_combo_box.setFixedSize(ls.FORM_FIELD_WIDTH, ls.CONTROL_HEIGHT)
        self.dependencies_module_combo_box.setMaxVisibleItems(12)
        self.dependencies_module_combo_box.setClearButtonEnabled(True)
        self.dependencies_module_combo_box.returnPressed.disconnect()
        self._dependency_names = QStringListModel(self)
        completer = QCompleter(self._dependency_names, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.dependencies_module_combo_box.setCompleter(completer)
        self.dependencies_import_button = self._action(icons.IMPORT_DEPENDENCY, "Import")
        self.dependencies_import_button.setDisabled(True)
        self.set_dependency_choices({})

        self.dependencies_table = self._table(4)
        self.bind_text(self.dependencies_table.setPlaceholderText, "Search for a dependency above and import it")
        self.dependencies_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.dependencies_remove_button = self._action(icons.REMOVE, "Remove")
        self.dependencies_remove_button.clicked.connect(lambda: self.remove_selected_rows(self.dependencies_table))
        layout.addWidget(self._table_card(
            self._form_row(
                icons.DEPENDENCIES, "Include Qt Dependencies",
                self.dependencies_module_combo_box, self.dependencies_import_button,
                caption="Libraries, plugins, QML modules",
            ),
            self.dependencies_table, self.dependencies_remove_button,
        ))

        layout.addStretch(1)
        self._set_table_headers()

    def _action(self, icon: FluentIconBase, label: str) -> PushButton:
        button = self.localized(PushButton(icon, "", self), label)
        button.setFixedSize(ls.ACTION_WIDTH, ls.CONTROL_HEIGHT)
        return button

    def _path_edit(self, placeholder: str) -> LineEdit:
        edit = LineEdit(self)
        edit.setReadOnly(True)
        edit.setAcceptDrops(False)
        edit.setFixedSize(ls.FORM_FIELD_WIDTH, ls.CONTROL_HEIGHT)
        self.bind_text(edit.setPlaceholderText, placeholder)
        edit.textChanged.connect(edit.setToolTip)
        return edit

    def _form_row(
        self, icon: FluentIconBase, title: str, *controls: QWidget, caption: str | None = None
    ) -> QHBoxLayout:
        heading = self.localized(BodyLabel(self), title)
        description = self.localized(CaptionLabel(self), caption) if caption is not None else None
        return ls.form_row(icon, heading, *controls, caption=description)

    def _table(self, columns: int, accept_drops: bool = False) -> PathTable:
        table = PathTable(self, accept_drops)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        table.setBorderVisible(True)
        table.setBorderRadius(8)
        table.setFixedHeight(ls.TABLE_HEIGHT)
        table.setColumnCount(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        return table

    def _table_card(self, header: QHBoxLayout, table: TableWidget, *buttons: PushButton) -> SimpleCardWidget:
        card = SimpleCardWidget()
        layout = QVBoxLayout(card)
        ls.set_card_padding(layout)

        banner = QWidget(card)
        banner.setFixedHeight(ls.TABLE_HEADER_HEIGHT)
        banner.setLayout(header)
        layout.addWidget(banner)
        layout.addWidget(table)
        layout.addLayout(ls.centered_actions(*buttons))
        return card

    def add_external_row(self, source: str, destination: str) -> bool:
        """Add an external file or folder unless it is already listed."""
        normalized = os.path.normcase(os.path.normpath(source))
        if any(os.path.normcase(os.path.normpath(row[0])) == normalized for row in self.external_rows()):
            return False
        table = self.external_table
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, table_item(source))
        table.setItem(row, 1, table_item(destination))
        return True

    def external_rows(self) -> list[tuple[str, str]]:
        table = self.external_table
        return [
            (source.text(), destination.text())
            for row in range(table.rowCount())
            if (source := table.item(row, 0)) and (destination := table.item(row, 1))
        ]

    def add_dependency_row(self, dependency, debug: bool) -> bool:
        """Add a Qt dependency unless it is already listed."""
        if any(item.name == dependency.name for item in self.dependencies()):
            return False
        table = self.dependencies_table
        row = table.rowCount()
        table.insertRow(row)
        artifact = dependency.build_entries(debug)[0]
        for column, value in enumerate(
            (dependency.name, tr(dependency.kind), artifact["Source"], artifact["Destination"])
        ):
            table.setItem(row, column, table_item(value))
        table.item(row, 0).setData(Qt.ItemDataRole.UserRole, dependency)
        table.item(row, 1).setData(Qt.ItemDataRole.UserRole, dependency.kind)
        return True

    def dependencies(self) -> list:
        table = self.dependencies_table
        return [
            dependency for row in range(table.rowCount())
            if (item := table.item(row, 0)) and (dependency := item.data(Qt.ItemDataRole.UserRole))
        ]

    def update_dependency_paths(self, debug: bool) -> None:
        table = self.dependencies_table
        for row, dependency in enumerate(self.dependencies()):
            artifact = dependency.build_entries(debug)[0]
            for column, field in ((2, "Source"), (3, "Destination")):
                item = table.item(row, column)
                item.setText(artifact[field])
                item.setToolTip(artifact[field])

    @staticmethod
    def remove_selected_rows(table: QTableWidget) -> None:
        for row in sorted({index.row() for index in table.selectedIndexes()}, reverse=True):
            table.removeRow(row)

    def reveal(self, widget: QWidget) -> None:
        self.findChild(SmoothScrollArea).ensureWidgetVisible(widget, 0, ls.FORM_CARD_HEIGHT)
        widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def dragEnterEvent(self, event) -> None:
        if project_file(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        path = project_file(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.project_dropped.emit(path)

    def _set_table_headers(self) -> None:
        self.external_table.setHorizontalHeaderLabels(
            [tr("Source Path (Local File / Folder)"), tr("Destination Path (In Package)")]
        )
        self.dependencies_table.setHorizontalHeaderLabels(
            [tr("Dependency"), tr("Type"), tr("Source Path"), tr("Package Path")]
        )

    def set_kit_search_state(self, scanning: bool, placeholder: str = "Select a Qt kit") -> None:
        self._kit_scanning = scanning
        self._kit_placeholder = placeholder
        self.env_button.setDisabled(scanning)
        self.env_button.setText(tr("Scanning..." if scanning else "Browse..."))
        if self.kit_combo.currentIndex() < 0:
            self.kit_combo.setPlaceholderText(tr(placeholder))

    def set_dependency_choices(self, choices: dict, *, kit_selected: bool = False) -> None:
        combo = self.dependencies_module_combo_box
        combo.clear()
        for label, dependency in choices.items():
            combo.addItem(label, userData=dependency)
        combo.setCurrentIndex(-1)
        self._dependency_names.setStringList(list(choices))
        combo.setEnabled(bool(choices))
        if choices:
            self._dependency_placeholder = "Search installed dependencies"
        elif kit_selected:
            self._dependency_placeholder = "No Qt dependencies available"
        else:
            self._dependency_placeholder = "Select a Qt kit first"
        self._update_dependency_placeholder()

    def _update_dependency_placeholder(self) -> None:
        self.dependencies_module_combo_box.setPlaceholderText(tr(self._dependency_placeholder))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._set_table_headers()
            self.set_kit_search_state(self._kit_scanning, self._kit_placeholder)
            self._update_dependency_placeholder()
            for row in range(self.dependencies_table.rowCount()):
                kind = self.dependencies_table.item(row, 1)
                if kind is not None:
                    text = tr(kind.data(Qt.ItemDataRole.UserRole))
                    kind.setText(text)
                    kind.setToolTip(text)


if __name__ == "__main__":
    sys.argv += ["-platform", "windows:darkmode=0"]
    app = QApplication(sys.argv)
    window = QtPackageSettingsUI()
    window.show()
    sys.exit(app.exec())
