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
import subprocess
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt, QEvent
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget, QTableWidgetItem
from PyQt6.QtGui import QTextDocument

from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    TeachingTip,
    InfoBarIcon,
    TeachingTipTailPosition,
)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from UI.ui_main import MainUI
from compiler import QtCompiler


# Logger
if getattr(sys, "frozen", False):
    log_filename = os.path.join(
        os.path.dirname(__file__), os.path.join("resource", "log", "app.log")
    )
else:
    log_filename = os.path.join(
        os.path.dirname(__file__), os.path.join("..", "resource", "log", "app.log")
    )
log_path = os.path.dirname(log_filename)
os.makedirs(log_path, exist_ok=True)
logger.remove()
logger.add(
    log_filename,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}.{function}:{line} - {message}",
    rotation="10 MB",
    retention="7 days",
    backtrace=True,
    diagnose=True,
)
try:
    logger.add(
        sys.stdout,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}.{function}:{line} - {message}",
        backtrace=True,
        diagnose=True,
    )
except Exception as e:
    logger.error(f"Error adding logger: {e}")


def exception_handler(exc_type, exc_value, exc_traceback):
    logger.error(f"Unhandled exception: {exc_type, exc_value, exc_traceback.tb_frame}")


sys.excepthook = exception_handler


class QtPackage(QWidget):
    emit_operation_status = pyqtSignal(int, str, int)

    def __init__(self):
        """
        Initialize the QtPackage class.
        """
        logger.info("Program started")

        super().__init__()

        self.ui = MainUI()
        self.compiler = QtCompiler()

        self.VALID_COMPILERS = ("mingw", "msvc")
        self.qt_compiler = {}
        self.qt_mingw = {}
        self.compiler_path = ""
        self.mingw_path = ""
        self.build_path: Path = None
        self.qt_project_file_path = ""
        self.qt_project_output_path = ""
        self.is_compiling = False

        self.set_connection()
        self.scan_qt_path()

        logger.info("Program initialized")

    def set_connection(self) -> None:
        """
        Connect signals and slots for the UI elements.
        """
        self.emit_operation_status.connect(self.info_bar)

        # Qt Packager Settings UI
        self.ui.qt_package_settings.env_button.clicked.connect(self.select_qt_folder)
        self.ui.qt_package_settings.project_button.clicked.connect(
            self.select_qt_project_file
        )
        self.ui.qt_package_settings.env_qt_version_combo_box.currentTextChanged.connect(
            self.qt_compiler_selection_changed
        )
        self.ui.qt_package_settings.env_qt_mingw_combo_box.currentTextChanged.connect(
            self.qt_mingw_selection_changed
        )

        self.ui.qt_package_settings.output_path_button.clicked.connect(
            self.select_output_directory
        )
        self.ui.qt_package_project.package_toggle_button.clicked.connect(
            self.packaging_toggle
        )
        self.ui.qt_package_project.package_folder_button.clicked.connect(
            self.open_build_folder
        )
        self.ui.qt_package_settings.external_file_button.clicked.connect(
            self.include_external_files
        )
        self.ui.qt_package_settings.external_folder_button.clicked.connect(
            self.include_external_folder
        )
        self.ui.qt_package_settings.external_delete_button.clicked.connect(
            self.delete_selected_rows
        )

        # Compiler
        self.compiler.output_signal.connect(self.handle_output)
        self.compiler.finished_signal.connect(self.handle_finished)
        self.compiler.error_signal.connect(self.handle_error)

        logger.info("Connections established")

    def is_qt_compiler_dir(self, path: str) -> bool:
        """
        Check if the given path is a valid Qt compiler directory.
        """
        dir_name = os.path.basename(path).lower()
        if not dir_name.startswith(self.VALID_COMPILERS):
            return False
        qmake_path = os.path.join(path, "bin", "qmake.exe")
        return os.path.exists(qmake_path)

    def get_qt_info(self, compiler_path: str) -> str:
        """
        Execute qmake -v to get Qt version and compiler information
        """
        qmake_path = os.path.join(compiler_path, "bin", "qmake.exe")
        try:
            result = subprocess.run(
                [qmake_path, "-v"], capture_output=True, text=True, check=True
            )
            output = result.stdout
            # Output example:
            # QMake version 3.1
            # Using Qt version 5.15.2 in C:/Qt/5.15.2/msvc2019_64/lib
            lines = output.splitlines()
            qt_line = next((line for line in lines if "Using Qt version" in line), "")
            qt_version = qt_line.split(" ")[3] if qt_line else "Unknown"
            compiler_name = os.path.basename(compiler_path)
            logger.info(f"Detected Qt version: {qt_version} in {compiler_name}")
            return f"Qt {qt_version} ({compiler_name})"
        except Exception as e:
            logger.warning(f"Invalid Qt info: {e}")
            return f"{os.path.basename(compiler_path)} (invalid qmake)"

    def scan_qt_path(self, folder: str = "C:\\Qt") -> None:
        """
        Scan the specified folder for Qt installations.
        """
        logger.info(f"Scanning: {folder}")
        self.ui.qt_package_settings.env_button.setDisabled(True)
        self.ui.qt_package_settings.env_button.setText("Scanning...")
        QApplication.processEvents()
        thread = threading.Thread(target=self.scan_qt_path_thread, args=(folder,))
        thread.start()

    def scan_qt_path_thread(self, folder) -> None:
        """
        Worker thread for scanning the Qt installation path.
        """
        qt_versions = self.find_qt_versions(folder)
        qt_mingw = self.find_qt_mingw_compilers(folder)

        QApplication.instance().postEvent(
            self, ScanFinishedEvent(qt_versions, qt_mingw)
        )

    def find_qt_mingw_compilers(self, path: str) -> dict:
        """
        Scan the Tools directory under the selected Qt root for all MinGW compilers,
        populate the env_combo_box, and save the mapping:
        qt_mingw: { "display_name": qmake_path }
        """
        qt_mingw = {}
        tools_dir = Path(path) / "Tools"
        if not tools_dir.is_dir():
            return qt_mingw

        mingw_dirs = [
            d
            for d in tools_dir.iterdir()
            if d.is_dir() and d.name.lower().startswith("mingw")
        ]

        def check_mingw(mingw_dir: Path):
            bin_dir = mingw_dir / "bin"
            gpp_path = bin_dir / "g++.exe"
            make_path = bin_dir / "mingw32-make.exe"

            if gpp_path.is_file() and make_path.is_file():
                arch = "64-bit" if "64" in mingw_dir.name else "32-bit"
                display_name = f"{mingw_dir.name} ({arch})"
                return display_name, str(bin_dir)
            return None

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(check_mingw, d) for d in mingw_dirs]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    display_name, bin_dir = result
                    qt_mingw[display_name] = bin_dir

        logger.info(f"Detected MinGW compilers: {qt_mingw}")
        return qt_mingw

    def find_qt_versions(self, path: str) -> dict:
        """
        Scan the Qt installation directory and populate the env_combo_box,
        while saving the dictionary mapping:
        qt_dict: { "display_name": compiler_path }
        """
        qt_compiler = {}
        path = Path(path)
        if not path.is_dir():
            return qt_compiler

        candidates = []
        for version in os.listdir(path):
            version_path = path / version
            if version_path.is_dir() and version[0].isdigit():
                for compiler in os.listdir(version_path):
                    compiler_path = version_path / compiler
                    if compiler_path.is_dir():
                        candidates.append(compiler_path)

        def check_compiler(compiler_path: Path):
            if self.is_qt_compiler_dir(compiler_path):
                display_name = self.get_qt_info(compiler_path)
                qmake_path = compiler_path / "bin"
                return display_name, str(qmake_path)
            return None

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(check_compiler, c) for c in candidates]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    display_name, qmake_path = result
                    qt_compiler[display_name] = qmake_path

        logger.info(f"Detected Qt compilers: {qt_compiler}")
        return qt_compiler

    def refresh_qt_version_combobox(self, qt_versions, qt_mingw) -> None:
        """
        Refresh the Qt version and MinGW combo boxes.
        """
        self.qt_compiler = qt_versions
        self.qt_mingw = qt_mingw
        self.ui.qt_package_settings.env_qt_version_combo_box.clear()
        self.ui.qt_package_settings.env_qt_mingw_combo_box.clear()

        if qt_versions:
            for display_name, path in qt_versions.items():
                self.ui.qt_package_settings.env_qt_version_combo_box.addItem(
                    display_name
                )
        else:
            self.ui.qt_package_settings.env_qt_version_combo_box.setPlaceholderText(
                "No Qt Found"
            )

        if qt_mingw:
            for display_name, path in qt_mingw.items():
                self.ui.qt_package_settings.env_qt_mingw_combo_box.addItem(display_name)
        else:
            self.ui.qt_package_settings.env_qt_mingw_combo_box.setPlaceholderText(
                "No MinGW Found"
            )

        self.ui.qt_package_settings.env_button.setDisabled(False)
        self.ui.qt_package_settings.env_button.setText("Browse...")

    @pyqtSlot()
    def select_qt_folder(self) -> None:
        """
        Open a file dialog to select the Qt installation folder.
        """
        folder = QFileDialog.getExistingDirectory(
            self.ui,
            "Select Qt Installation Folder",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            logger.info(f"User selected Qt folder: {folder}")
            self.scan_qt_path(folder)

    @pyqtSlot()
    def select_qt_project_file(self) -> None:
        """
        Open a file dialog to select a Qt project file (.pro).
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self.ui,
            "Select Qt Project File",
            "",
            "Qt Project Files (*.pro)",
        )
        if not self.is_english_path(file_path):
            logger.warning(
                f"User selected a project file with invalid path characters: {file_path}"
            )
            self.emit_operation_status.emit(
                0, "Invalid Path: Selected path contains invalid characters", 5000
            )
            return

        if file_path:
            logger.info(f"User selected project: {file_path}")
            self.ui.qt_package_settings.project_select_edit.setText(file_path)
            self.qt_project_file_path = file_path

    @pyqtSlot(str)
    def qt_compiler_selection_changed(self, text: str) -> None:
        """
        Handle the change of the Qt compiler selection.
        """
        compiler_path = self.qt_compiler.get(text, None)
        if compiler_path:
            logger.info(f"User selected Qt compiler path: {compiler_path}")
            self.compiler_path = compiler_path

    @pyqtSlot(str)
    def qt_mingw_selection_changed(self, text: str) -> None:
        """
        Handle the change of the Qt MinGW selection.
        """
        mingw_path = self.qt_mingw.get(text, None)
        if mingw_path:
            logger.info(f"User selected Qt MinGW path: {mingw_path}")
            self.mingw_path = mingw_path

    @pyqtSlot()
    def select_output_directory(self) -> None:
        """
        Open a dialog for the user to select a project output directory.
        """
        dir_path = QFileDialog.getExistingDirectory(
            self.ui,
            "Select Project Output Directory",
            "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )

        if not self.is_english_path(dir_path):
            logger.warning(
                f"User selected an output directory with invalid path characters: {dir_path}"
            )
            self.emit_operation_status.emit(
                0, "Invalid Path: Selected path contains invalid characters", 5000
            )
            return

        if dir_path:
            logger.info(f"User selected output directory: {dir_path}")
            self.ui.qt_package_settings.output_path_edit.setText(dir_path)
            self.qt_project_output_path = dir_path

            if os.listdir(dir_path):  # Folder is not empty
                logger.warning(
                    f"User selected a non-empty folder as output directory: {dir_path}"
                )
                TeachingTip.create(
                    target=self.ui.qt_package_settings.output_path_edit,
                    icon=InfoBarIcon.WARNING,
                    title="Output Folder Not Empty",
                    content="To avoid packaging errors, it is recommended to use an empty folder.",
                    isClosable=True,
                    tailPosition=TeachingTipTailPosition.BOTTOM,
                    duration=5000,
                    parent=self,
                )

    def is_english_path(self, path: str) -> bool:
        """
        Check if the path and its parent directories contain only English characters, numbers, and common symbols.
        """
        # Allowed half-width characters: English uppercase and lowercase + numbers + _ / \ : . -
        allowed_pattern = r"^[A-Za-z0-9_:/\\.-]+$"

        # Convert the path to an absolute path and split it
        path = Path(path).resolve()
        for part in path.parts:
            if not re.match(allowed_pattern, part):
                return False
        return True

    @pyqtSlot()
    def packaging_toggle(self) -> None:
        """
        Start the packaging process using the selected Qt compiler and project file.
        """
        if not self.is_compiling:
            compiler_text = (
                self.ui.qt_package_settings.env_qt_version_combo_box.currentText()
            )

            if not self.compiler_path:
                self.ui.qt_package_project.package_terminal.append(
                    "No valid Qt compiler selected."
                )
                return

            if not self.mingw_path:
                self.ui.qt_package_project.package_terminal.append(
                    "No valid MinGW path selected."
                )
                return

            if not self.qt_project_file_path:
                self.ui.qt_package_project.package_terminal.append(
                    "No project file selected."
                )
                return

            if not self.qt_project_output_path:
                self.ui.qt_package_project.package_terminal.append(
                    "No output directory selected."
                )
                return

            try:
                logger.info(f"Starting packaging with {compiler_text}...")
                self.ui.qt_package_project.package_terminal.append(
                    f"Starting packaging with {compiler_text}..."
                )
                self.compiler.compile_qt_project(
                    self.qt_project_file_path,
                    self.compiler_path,
                    self.mingw_path,
                    self.qt_project_output_path,
                    self.extract_table_data(),
                    self.ui.qt_package_settings.build_combo_box.currentText()
                    == "Release",
                    self.ui.qt_package_settings.clean_switch.isChecked(),
                )
                self.is_compiling = True

            except Exception as e:
                logger.error(f"Error occurred while packaging: {str(e)}")
                self.ui.qt_package_project.package_terminal.append(
                    f'<span style="color:red; font-weight:bold;">Error: {str(e)}</span>'
                )

        else:
            logger.info("Stopping packaging process...")
            self.is_compiling = False
            self.compiler.stop_process()

        self.ui.qt_package_project.package_toggle_button.setText(
            "Stop Packaging" if self.is_compiling else "Start Packaging"
        )
        self.ui.qt_package_project.package_toggle_button.setIcon(
            FIF.CLOSE if self.is_compiling else FIF.PLAY
        )
        self.ui.qt_package_settings.setDisabled(self.is_compiling)
        self.ui.qt_package_project.package_folder_button.setDisabled(True)

    @pyqtSlot(str)
    def handle_output(self, text: str) -> None:
        """
        Handle normal output messages.
        """
        if re.search(r"error", text, re.IGNORECASE):
            self.ui.qt_package_project.package_terminal.append(
                f'<span style="color:orange; font-weight:bold;">[Error] {text}</span>'
            )
        else:
            self.ui.qt_package_project.package_terminal.append(text)

    @pyqtSlot(Path)
    def handle_finished(self, build_dir: Path) -> None:
        """
        Handle compiler finished signal.
        """
        self.is_compiling = False
        self.build_path = build_dir
        self.ui.qt_package_project.package_toggle_button.setText(
            "Stop Packaging" if self.is_compiling else "Start Packaging"
        )
        self.ui.qt_package_project.package_toggle_button.setIcon(
            FIF.CLOSE if self.is_compiling else FIF.PLAY
        )
        self.ui.qt_package_settings.setDisabled(self.is_compiling)
        self.ui.qt_package_project.package_folder_button.setDisabled(False)
        self.emit_operation_status.emit(1, "Build & Deploy finished", 2000)

    @pyqtSlot(str)
    def handle_error(self, msg: str) -> None:
        """
        Handle error messages.
        """
        self.is_compiling = False
        self.ui.qt_package_project.package_toggle_button.setText(
            "Stop Packaging" if self.is_compiling else "Start Packaging"
        )
        self.ui.qt_package_project.package_toggle_button.setIcon(
            FIF.CLOSE if self.is_compiling else FIF.PLAY
        )
        self.ui.qt_package_settings.setDisabled(self.is_compiling)

        self.ui.qt_package_project.package_terminal.append(
            f'<span style="color:red; font-weight:bold;">[Error] {msg}</span>'
        )
        self.emit_operation_status.emit(0, msg, 2000)

    @pyqtSlot()
    def open_build_folder(self) -> None:
        """
        Open the build folder in the file explorer.
        """
        if self.build_path.exists() and self.build_path.is_dir():
            logger.info(f"Opening build folder: {self.build_path}")
            subprocess.Popen(f'explorer "{self.build_path}"')
        else:
            logger.warning(f"Folder does not exist: {self.build_path}")
            self.emit_operation_status.emit(
                0, f"Build folder does not exist: {self.build_path}", 2000
            )

    @pyqtSlot()
    def include_external_folder(self) -> None:
        """
        Include an external folder.
        """
        folder_path = QFileDialog.getExistingDirectory(self.ui, "Select a Folder")
        if folder_path:
            logger.info(f"User selected external folder to include: {folder_path}")
            self.add_path_to_table(folder_path)

    @pyqtSlot()
    def include_external_files(self) -> None:
        """
        Include external files.
        """
        file_paths, _ = QFileDialog.getOpenFileNames(self.ui, "Select Files")
        if file_paths:
            for file_path in file_paths:
                logger.info(f"User selected external file to include: {file_path}")
                self.add_path_to_table(file_path)

    def add_path_to_table(self, path: str) -> None:
        """
        Add a file or folder path to the external resources table.
        """
        if not path:
            logger.warning(f"Invalid path provided for external resources: ({path})")
            self.emit_operation_status.emit(0, "Invalid path", 2000)
            return

        if os.path.isfile(path) or os.path.isdir(path):
            # Destination Path = / + basename
            dest_path = "/" + os.path.basename(path)
        else:
            # Invalid path
            logger.warning(f"Invalid path provided for external resources: ({path})")
            self.emit_operation_status.emit(0, "Invalid path", 2000)
            return

        # Add a new row
        row = self.ui.qt_package_settings.external_table.rowCount()
        self.ui.qt_package_settings.external_table.insertRow(row)

        # Source Path
        src_item = QTableWidgetItem(path)
        src_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ui.qt_package_settings.external_table.setItem(row, 0, src_item)

        # Destination Path
        dest_item = QTableWidgetItem(dest_path)
        dest_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ui.qt_package_settings.external_table.setItem(row, 1, dest_item)

    def delete_selected_rows(self) -> None:
        """
        Delete selected rows from the QTableWidget.
        """
        # Get selected row indexes
        selected_rows = set(
            index.row()
            for index in self.ui.qt_package_settings.external_table.selectedIndexes()
        )

        # Delete from bottom to top to avoid index shifting
        for row in sorted(selected_rows, reverse=True):
            self.ui.qt_package_settings.external_table.removeRow(row)

        logger.info(f"Selected rows deleted: {selected_rows}")

    def extract_table_data(self) -> list[dict]:
        """
        Extract data from the QTableWidget and return it as a list of dictionaries.
        """
        data = []
        row_count = self.ui.qt_package_settings.external_table.rowCount()
        col_count = self.ui.qt_package_settings.external_table.columnCount()
        headers = [
            self.ui.qt_package_settings.external_table.horizontalHeaderItem(i).text()
            for i in range(col_count)
        ]

        for row in range(row_count):
            row_data = {}
            for col in range(col_count):
                item = self.ui.qt_package_settings.external_table.item(row, col)
                row_data[headers[col]] = item.text() if item else ""
            data.append(row_data)

        logger.info(f"Extracted table data: {data}")
        return data

    @pyqtSlot(int, str, int)
    def info_bar(self, state: int, message: str, time: int = 2000):
        """
        Show an informational message in the info bar.
        """
        message = self.html_to_plain_text(message)
        succTitle = "Success"
        failTitle = "Failed"
        infoTitle = "Notification"

        if state == 1:
            InfoBar.success(
                title=succTitle,
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=time,
                parent=self.ui,
            )
        elif state == 0:
            InfoBar.error(
                title=failTitle,
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=time,
                parent=self.ui,
            )
        elif state == -1:
            InfoBar.info(
                title=infoTitle,
                content=message,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=time,
                parent=self.ui,
            )

    def html_to_plain_text(self, html: str) -> str:
        """Convert HTML content to plain text using QTextDocument."""
        if not html:
            return ""
        doc = QTextDocument()
        doc.setHtml(html)
        return doc.toPlainText()

    def event(self, e):
        """
        Handle custom events.
        """
        if e.type() == ScanFinishedEvent.EVENT_TYPE:
            self.refresh_qt_version_combobox(e.qt_versions, e.qt_mingw)
            return True
        return super().event(e)


class ScanFinishedEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, qt_versions, qt_mingw):
        super().__init__(ScanFinishedEvent.EVENT_TYPE)
        self.qt_versions = qt_versions
        self.qt_mingw = qt_mingw


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = QtPackage()
    w.ui.show()
    app.exec()
    logger.info("Program exited")
