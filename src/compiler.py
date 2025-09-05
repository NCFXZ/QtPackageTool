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
import shutil
from pathlib import Path
from loguru import logger
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
        self._copy_sources_started = False
        self._qml_dll_deploy_count = 0

        self.output_path = ""
        self.qt_bin = ""
        self.mingw_bin = ""
        self.project_file = ""
        self.external_sources = []
        self._qml_dll_need_deploy_files = []
        self.is_release = False
        self.need_clean = False

        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.process_finished)

    def compile_qt_project(
        self,
        project_file: str,
        qt_bin: str,
        mingw_bin: str,
        output_path: str,
        external_sources: list[dict],
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

        self._make_started = False
        self._deploy_started = False
        self._copy_sources_started = False
        self._qml_dll_deploy_count = 0

        self.output_path = output_path
        self.qt_bin = qt_bin
        self.mingw_bin = mingw_bin
        self.project_file = project_file
        self.external_sources = external_sources
        self.is_release = is_release
        self.need_clean = need_clean
        logger.info(
            f"project_file: {project_file}, output_path: {output_path}, qt_bin: {qt_bin}, mingw_bin: {mingw_bin}, external_sources: {external_sources}, is_release: {is_release}, need_clean: {need_clean}"
        )

        # Check whether QML DLL files need to be deployed
        self._qml_dll_need_deploy_files = []
        for entry in external_sources:
            type = entry.get("Type")
            if type.strip().casefold() == "qml":
                self._qml_dll_need_deploy_files.append(entry.get("Destination"))
        logger.info(f"QML deployment required for: {self._qml_dll_need_deploy_files}")

        # Step 1: qmake
        config_type = "release" if is_release else "debug"
        qmake_exe = (Path(qt_bin) / "qmake.exe").resolve()
        if not qmake_exe.exists():
            logger.error(f"qmake not found: {qmake_exe}")
            self.error_signal.emit(f"qmake not found: {qmake_exe}")
            return

        args = [project_file, f"CONFIG+={config_type}"]
        logger.info(f"Setting working directory: {output_path}")
        logger.info(f"Running: {qmake_exe} {args}")
        self.output_signal.emit("Running qmake...\n")
        self.process.setWorkingDirectory(output_path)
        self.process.start(str(qmake_exe), args)

    def handle_stdout(self) -> None:
        """
        Handle standard output from the process.
        """
        data = self.process.readAllStandardOutput()
        text = bytes(data).decode("utf-8")
        logger.info(f"Compiler output: {text.strip()}")
        self.output_signal.emit(text)

    def handle_stderr(self) -> None:
        """
        Handle standard error from the process.
        """
        data = self.process.readAllStandardError()
        text = bytes(data).decode("utf-8")
        logger.error(f"Compiler error: {text.strip()}")
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

        logger.info(f"Executable name derived from .pro file: {exe_name}")
        return exe_name + ".exe"

    def process_finished(self) -> None:
        """
        Handle process finished event.
        """
        exit_code = self.process.exitCode()
        if (
            self.process.exitStatus() == QProcess.ExitStatus.NormalExit
            and exit_code == 0
        ):
            if not self._make_started:
                # Step 1: qmake finished, start make
                self._make_started = True
                make_tool = (Path(self.mingw_bin) / "mingw32-make.exe").resolve()
                if not make_tool.exists():
                    logger.error(f"Make tool not found: {make_tool}")
                    self.error_signal.emit(f"Make tool not found: {make_tool}")
                    return

                logger.info(f"Running: {make_tool}")
                self.output_signal.emit("Running make...\n")
                self.process.start(str(make_tool))

            elif not self._deploy_started:
                # Step 2: make finished, start windeployqt
                self._deploy_started = True
                windeploy_exe = (Path(self.qt_bin) / "windeployqt.exe").resolve()
                exe_name = self.get_exe_name_from_pro(self.project_file)
                config_dir = "release" if self.is_release else "debug"
                exe_path = (Path(self.output_path) / config_dir / exe_name).resolve()

                if not exe_path.exists():
                    logger.error(f"Executable not found: {exe_path}")
                    self.error_signal.emit(f"Executable not found: {exe_path}")
                    return

                logger.info(f"Running: {windeploy_exe} {[exe_path]}")
                self.output_signal.emit("Running windeployqt...\n")
                self.process.start(str(windeploy_exe), [str(exe_path)])

            elif not self._copy_sources_started and self.external_sources:
                # Step 3: Copy sources
                self._copy_sources_started = True
                config_dir = "release" if self.is_release else "debug"
                build_dir = (Path(self.output_path) / config_dir).resolve()
                logger.info(f"Copying external sources to: {build_dir}")
                self.output_signal.emit("Copying external sources...\n")
                self.copy_sources_to_output(self.external_sources, str(build_dir))

            elif self._qml_dll_deploy_count < len(self._qml_dll_need_deploy_files):
                # Step 4: Deploy QML DLLs
                dll = self._qml_dll_need_deploy_files[self._qml_dll_deploy_count]
                config_dir = "release" if self.is_release else "debug"
                dll_path = (
                    Path(self.output_path) / config_dir / dll.lstrip("/\\")
                ).resolve()
                windeploy_exe = os.path.join(self.qt_bin, "windeployqt.exe")
                qml_path = (Path(self.qt_bin).parent / "qml").resolve()

                if not os.path.exists(windeploy_exe) or not os.path.exists(qml_path):
                    logger.error(f"Path not found: {windeploy_exe} or {qml_path}")
                    self.error_signal.emit(
                        f"Path not found: {windeploy_exe} or {qml_path}"
                    )
                    return

                logger.info(f"Running: {windeploy_exe} {dll_path} --qmldir {qml_path}")
                self.output_signal.emit(f"Deploying QML DLL: {dll_path}\n")
                self.process.start(
                    windeploy_exe, [str(dll_path), "--qmldir", str(qml_path)]
                )
                self._qml_dll_deploy_count += 1

            else:
                config_dir = "release" if self.is_release else "debug"
                build_dir = (Path(self.output_path) / config_dir).resolve()
                logger.info(f"Build successful to directory: {build_dir}")
                self.finished_signal.emit(build_dir)

                if self.need_clean:
                    logger.info(f"Cleaning build files in: {build_dir}")
                    self.clean_build_files()

                self.output_signal.emit(
                    '<span style="color:green; font-weight:bold;">Packaging finished!</span>'
                )

        else:
            if exit_code != 0:
                if self._make_started:
                    logger.error(f"Build failed with exit code {exit_code}")
                    self.error_signal.emit(f"Build failed with exit code {exit_code}")
                elif self._deploy_started:
                    logger.error(f"Deployment failed with exit code {exit_code}")
                    self.error_signal.emit(
                        f"Deployment failed with exit code {exit_code}"
                    )

            elif self.process.exitStatus() != QProcess.ExitStatus.NormalExit:
                logger.error(f"Process exited abnormally, code: {exit_code}")
                self.error_signal.emit(f"Process exited abnormally, code: {exit_code}")

    def copy_sources_to_output(self, external_source, output_dir: str) -> None:
        """
        Copy source files and folders to the output directory based on the external source.
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        for entry in external_source:
            src = entry.get("Source")
            dest_rel = entry.get("Destination")
            logger.info(f"Try to copy {src} to {dest_rel}")

            if not src or not dest_rel:
                logger.warning(f"Invalid entry: {entry}")
                self.output_signal.emit(f"Invalid entry, skipping: {entry}")
                continue

            dest = os.path.join(output_dir, dest_rel.lstrip("/"))

            try:
                if os.path.isfile(src):
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copy2(src, dest)
                    logger.info(f"Copied {src} -> {dest}")
                    self.output_signal.emit(f"Copied {src} -> {dest}")
                elif os.path.isdir(src):
                    shutil.copytree(src, dest, dirs_exist_ok=True)
                    logger.info(f"Copied {src} -> {dest}")
                    self.output_signal.emit(f"Copied {src} -> {dest}")

            except Exception as e:
                logger.error(f"Failed to copy {src} -> {dest}: {e}")
                self.error_signal.emit(f"Failed to copy {src} -> {dest}: {e}")
                return

        logger.info("All external sources copied successfully.")
        self.process_finished()  # Trigger the next step after copying sources

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
                    logger.error(f"Could not delete {file_path}: {e}")
                    self.output_signal.emit(
                        f"[Error] Could not delete {file_path}: {e}\n"
                    )

        logger.info(f"Cleaned {removed_files} build artifact files.")
        self.output_signal.emit(f"Cleaned {removed_files} build artifact files.\n")

    def stop_process(self) -> None:
        """
        Stop the current process if it is running.
        """
        if self.process is not None:
            self.process.kill()
            logger.info("Build process stopped by user.")
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
