import os
import sys
import subprocess
from pathlib import Path

from PyQt6.QtCore import pyqtSignal, pyqtSlot, Qt
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget
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


class QtPackage(QWidget):
    emit_operation_status = pyqtSignal(int, str, int)

    def __init__(self):
        """
        Initialize the QtPackage class.
        """
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
        self.find_qt_versions()
        self.find_qt_mingw_compilers()

    def set_connection(self) -> None:
        """
        Connect signals and slots for the UI elements.
        """
        self.emit_operation_status.connect(self.info_bar)

        # Qt Packager Settings UI
        self.ui.qt_package_settings.env_button.clicked.connect(self.browse_qt_folder)
        self.ui.qt_package_settings.project_button.clicked.connect(
            self.browse_project_file
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

        # Compiler
        self.compiler.output_signal.connect(self.handle_output)
        self.compiler.finished_signal.connect(self.handle_finished)
        self.compiler.error_signal.connect(self.handle_error)

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
            return f"Qt {qt_version} ({compiler_name})"
        except Exception as e:
            return f"{os.path.basename(compiler_path)} (invalid qmake)"

    def find_qt_mingw_compilers(self, path: str = None) -> dict:
        """
        Scan the Tools directory under the selected Qt root for all MinGW compilers,
        populate the env_combo_box, and save the mapping:
        self.qt_mingw: { "display_name": qmake_path }
        """

        self.qt_mingw = {}  # Clear previous mapping
        self.ui.qt_package_settings.env_qt_mingw_combo_box.clear()

        if not path:
            qt_root = "C:\\Qt"
        else:
            qt_root = path

        qt_root = Path(qt_root)
        tools_dir = qt_root / "Tools"
        if not tools_dir.exists():
            self.ui.qt_package_settings.env_qt_mingw_combo_box.setPlaceholderText(
                "No MinGW found"
            )
            return

        # 遍历 Tools 下所有 mingw* 目录（32-bit 和 64-bit）
        for mingw_dir in tools_dir.glob("mingw*"):
            bin_dir = mingw_dir / "bin"
            gpp_path = bin_dir / "g++.exe"
            make_path = bin_dir / "mingw32-make.exe"

            if gpp_path.exists() and make_path.exists():
                arch = "64-bit" if "64" in mingw_dir.name else "32-bit"
                display_name = f"{mingw_dir.name} ({arch})"
                self.qt_mingw[display_name] = str(bin_dir)
                self.ui.qt_package_settings.env_qt_mingw_combo_box.addItem(display_name)

        if not self.qt_mingw:
            self.ui.qt_package_settings.env_qt_mingw_combo_box.setPlaceholderText(
                "No MinGW found"
            )

    def find_qt_versions(self, path: str = None) -> None:
        """
        Scan the Qt installation directory and populate the env_combo_box,
        while saving the dictionary mapping:
        self.qt_dict: { "display_name": compiler_path }
        """
        self.qt_compiler = {}  # Clear the dictionary
        self.ui.qt_package_settings.env_qt_version_combo_box.clear()

        if not path:
            qt_root = "C:\\Qt"
        else:
            qt_root = path

        qt_root = Path(qt_root)
        if not qt_root.exists():
            self.ui.qt_package_settings.env_qt_version_combo_box.setPlaceholderText(
                "No Qt version found"
            )
            return

        for version in os.listdir(qt_root):
            version_path = os.path.join(qt_root, version)
            if os.path.isdir(version_path) and version[0].isdigit():
                for compiler in os.listdir(version_path):
                    compiler_path = os.path.join(version_path, compiler)
                    if os.path.isdir(compiler_path) and self.is_qt_compiler_dir(
                        compiler_path
                    ):
                        display_name = self.get_qt_info(compiler_path)
                        qmake_path = os.path.join(compiler_path, "bin")
                        self.qt_compiler[display_name] = qmake_path
                        self.ui.qt_package_settings.env_qt_version_combo_box.addItem(
                            display_name
                        )

        if not self.qt_compiler:
            self.ui.qt_package_settings.env_qt_version_combo_box.setPlaceholderText(
                "No Qt version found"
            )

    @pyqtSlot()
    def browse_qt_folder(self) -> None:
        """
        Open a file dialog to select the Qt installation folder.
        """
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Qt Installation Folder",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            print(f"User selected: {folder}")
            self.find_qt_versions(folder)

    @pyqtSlot()
    def browse_project_file(self) -> None:
        """
        Open a file dialog to select a Qt project file (.pro).
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Qt Project File",
            "",
            "Qt Project Files (*.pro)",
        )
        if file_path:
            print(f"User selected project: {file_path}")
            self.ui.qt_package_settings.project_select_edit.setText(file_path)
            self.qt_project_file_path = file_path

    @pyqtSlot(str)
    def qt_compiler_selection_changed(self, text: str) -> None:
        """
        Handle the change of the Qt compiler selection.
        """
        compiler_path = self.qt_compiler.get(text, None)
        if compiler_path:
            self.compiler_path = compiler_path
            print(f"Selected Qt compiler path: {compiler_path}")

    @pyqtSlot(str)
    def qt_mingw_selection_changed(self, text: str) -> None:
        """
        Handle the change of the Qt MinGW selection.
        """
        mingw_path = self.qt_mingw.get(text, None)
        if mingw_path:
            self.mingw_path = mingw_path
            print(f"Selected Qt MinGW path: {mingw_path}")

    @pyqtSlot()
    def select_output_directory(self) -> None:
        """
        Open a dialog for the user to select a project output directory.
        """
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Project Output Directory",
            "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )

        if dir_path:
            print(f"Selected output directory: {dir_path}")
            self.ui.qt_package_settings.output_path_edit.setText(dir_path)
            self.qt_project_output_path = dir_path

            if os.listdir(dir_path):  # Folder is not empty
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
                self.ui.qt_package_project.package_terminal.append(
                    f"Starting packaging with {compiler_text}..."
                )
                self.compiler.compile_qt_project(
                    self.qt_project_file_path,
                    self.compiler_path,
                    self.mingw_path,
                    self.qt_project_output_path,
                    self.ui.qt_package_settings.build_combo_box.currentText()
                    == "Release",
                    self.ui.qt_package_settings.clean_switch.isChecked(),
                )
                self.is_compiling = True

            except Exception as e:
                self.ui.qt_package_project.package_terminal.append(
                    f'<span style="color:red; font-weight:bold;">Error: {str(e)}</span>'
                )

        else:
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
            subprocess.Popen(f'explorer "{self.build_path}"')
        else:
            print(f"Folder does not exist: {self.build_path}")
            self.emit_operation_status.emit(
                0, f"Build folder does not exist: {self.build_path}", 2000
            )

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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = QtPackage()
    w.ui.show()
    app.exec()
