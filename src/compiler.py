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
import glob
from pathlib import Path
from PyQt6.QtCore import QObject, QProcess, pyqtSignal, QProcessEnvironment


class QtCompiler(QObject):
    output_signal = pyqtSignal(str)  # Output information
    finished_signal = pyqtSignal(Path)  # Compilation finished
    error_signal = pyqtSignal(str)  # Compilation error

    def __init__(self):
        """
        Initialize the QtCompiler class.
        """
        super().__init__()
        self.process = QProcess(self)
        self._make_started = False
        self._deploy_started = False

        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.process_finished)

    def compile_qt_project(
        self,
        project_file: str,
        qt_bin: str,
        mingw_bin: str,
        output_path: str,
        is_release: bool,
        need_clean: bool,
    ) -> None:
        """
        Compile a Qt project using QProcess.
        """
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.stop_process()

        # Ensure build directory exists
        os.makedirs(output_path, exist_ok=True)

        # Configure environment variables
        env = os.environ.copy()
        env["PATH"] += ";" + qt_bin + ";" + mingw_bin
        self.process.setProcessEnvironment(self._dict_to_qt_env(env))

        self.output_path = output_path
        self.qt_bin = qt_bin
        self.mingw_bin = mingw_bin
        self.project_file = project_file
        self.is_release = is_release
        self.need_clean = need_clean

        # Step 1: qmake
        config_type = "release" if is_release else "debug"
        qmake_exe = os.path.join(qt_bin, "qmake.exe")
        args = [project_file, f"CONFIG+={config_type}"]

        self.output_signal.emit("Running qmake...\n")
        self.process.setWorkingDirectory(output_path)
        self.process.start(qmake_exe, args)

    def handle_stdout(self) -> None:
        """
        Handle standard output from the process.
        """
        data = self.process.readAllStandardOutput()
        text = bytes(data).decode("utf-8")
        self.output_signal.emit(text)

    def handle_stderr(self) -> None:
        """
        Handle standard error from the process.
        """
        data = self.process.readAllStandardError()
        text = bytes(data).decode("utf-8")
        self.output_signal.emit("[Error] " + text)

    def get_exe_name_from_pro(self, project_file: str) -> str:
        """
        Get the executable name from the .pro file.
        """
        exe_name = os.path.splitext(os.path.basename(project_file))[
            0
        ]  # Default: same name as .pro
        with open(project_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().lower().startswith("target"):
                    parts = line.split("=")
                    if len(parts) > 1:
                        exe_name = parts[1].strip()
        return exe_name + ".exe"

    def process_finished(self) -> None:
        """
        Handle process finished event.
        """
        if self.process.exitStatus() == QProcess.ExitStatus.NormalExit:
            if not self._make_started:
                # Step 1: qmake finished, start make
                self._make_started = True
                make_tool = os.path.join(self.mingw_bin, "mingw32-make.exe")
                self.output_signal.emit("Running make...\n")
                self.process.start(make_tool)
            elif not self._deploy_started:
                # Step 2: make finished, start windeployqt
                self._deploy_started = True
                windeploy_exe = os.path.join(self.qt_bin, "windeployqt.exe")

                exe_name = self.get_exe_name_from_pro(self.project_file)
                config_dir = "release" if self.is_release else "debug"
                exe_path = os.path.join(self.output_path, config_dir, exe_name)

                if not os.path.exists(exe_path):
                    self.error_signal.emit(f"Executable not found: {exe_path}")
                    return

                self.output_signal.emit("Running windeployqt...\n")
                self.process.start(windeploy_exe, [exe_path])
            else:
                config_dir = "release" if self.is_release else "debug"
                build_dir = Path(self.output_path) / config_dir
                self.finished_signal.emit(build_dir)

                if self.need_clean:
                    self.clean_build_files()
                self._make_started = False
                self._deploy_started = False

                self.output_signal.emit(
                    '<span style="color:green; font-weight:bold;">Build & Deploy finished!</span>'
                )

        else:
            self._make_started = False
            self._deploy_started = False

            self.error_signal.emit(
                f"Process exited abnormally, code: {self.process.exitCode()}"
            )

    def clean_build_files(self) -> None:
        """
        Delete all .cpp, .c, and .o files under the build directory.
        """
        if not hasattr(self, "output_path") or not os.path.exists(self.output_path):
            return

        # Traverse release/debug directories
        config_dir = "release" if self.is_release else "debug"
        build_dir = os.path.join(self.output_path, config_dir)

        patterns = ["*.cpp", "*.c", "*.o"]
        removed_files = 0

        for pattern in patterns:
            for file_path in glob.glob(os.path.join(build_dir, pattern)):
                try:
                    os.remove(file_path)
                    removed_files += 1
                except Exception as e:
                    self.output_signal.emit(
                        f'<span style="color:orange; font-weight:bold;">[Warning] Could not delete {file_path}: {e}</span>\n'
                    )

        self.output_signal.emit(f"Cleaned {removed_files} build artifact files.\n")

    def stop_process(self) -> None:
        """
        Stop the current process if it is running.
        """
        if self.process is not None:
            self.process.kill()
            self.error_signal.emit(
                '<span style="color:red; font-weight:bold;">Build stopped by user</span>'
            )

    @staticmethod
    def _dict_to_qt_env(env_dict: dict) -> QProcessEnvironment:
        """Convert Python dictionary to QProcessEnvironment"""
        qt_env = QProcessEnvironment()
        for k, v in env_dict.items():
            qt_env.insert(k, v)
        return qt_env
