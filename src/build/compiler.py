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

import filecmp
import os
import re
import shutil
import tempfile
from enum import Enum
from itertools import count
from pathlib import Path
from threading import Event

import winerror
from loguru import logger
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from build.process import BuildProcess
from build import cmake, qmake, qml
from build.cmake import CMAKE_BUILD_DIR, CMAKE_PACKAGE_DIR, is_cmake_project
from build.paths import deployment_path, entry_status, remove_readonly, resource_source
from build.qmake import is_qmake_intermediate
from build.qml import QML_SOURCE_COPY
from app.localization import tr
from deployment.qt_dependencies import QML_MODULE, QT_LIBRARY, QT_PLUGIN, qt_debug_runtime, qt_major_version, runtime_binary
from deployment.runtime_dependencies import check_qml_modules, qml_runtime_dirs, read_qml_modules, validate_startup_libraries
from toolchains.discovery import MSVC, Toolchain, find_build_tool, msvc_environment_command, parse_msvc_environment


BUILD_STOPPED_MESSAGE = "Build stopped by user."
# Compiler, linker, make, Ninja and CMake error lines; "-Werror" and "0 errors" are not errors.
ERROR_LINE = re.compile(
    r"(?:^|[\s:(])(?:fatal )?error(?: [A-Z]+\d+)?\s*:|\*\*\*.*\bError \d+|^FAILED:|^CMake Error|^\[Error\]",
    re.IGNORECASE | re.MULTILINE,
)


class BuildPhase(Enum):
    PREPARING = "Preparing build"
    COMPILING = "Compiling project"
    DEPLOYING = "Deploying dependencies and files"
    VALIDATING = "Validating package"
    PUBLISHING = "Publishing package"


class Stage(Enum):
    """Steps of one build. The values are shown in the build log."""

    PREPARATION = "Build preparation"
    QMAKE = "qmake"
    CMAKE_CONFIGURE = "CMake configure"
    BUILD = "build"
    QT_STAGING = "Qt dependency staging"
    DEPLOY = "windeployqt"
    RESOURCE_COPY = "Resource copy"
    QML_CHECK = "QML dependency check"
    QML_SOURCE_CHECK = "QML source import check"
    VALIDATION = "Package validation"
    PUBLISHING = "Publishing"


class BuildCancelled(Exception):
    pass


def is_error_line(text: str) -> bool:
    return ERROR_LINE.search(text) is not None


class QtCompiler(QObject):
    output_signal = pyqtSignal(str)  # Output information
    progress_signal = pyqtSignal(object)  # BuildPhase
    finished_signal = pyqtSignal(Path)  # Compilation finished
    error_signal = pyqtSignal(str)  # Compilation error

    def __init__(self):
        """
        Initialize the QtCompiler class.
        """
        super().__init__()
        self.process = BuildProcess(self)
        self._stage = None
        self._cancel_event = Event()
        self._shutting_down = False
        self._environment = {}

        self.output_path = ""
        self.qt_bin = ""
        self.compiler = None
        self.project_file = ""
        self.external_sources = []
        self._qml_import_paths = []
        self._resource_collections = []
        self._qml_resource_sources = set()
        self._qml_source_list = None
        self._manual_qml_file = None
        self._qml_runtime = None
        self._qml_plugins = set()
        self._qml_scan_output = []
        self._qml_scan_diagnostics = []
        self._optional_qml_imports = set()
        self._msvc_env_output = []
        self._msvc_vcvars = None
        self._artifact = None
        self._build_root = None
        self._publication_dir = None
        self._cmake = None
        self._cmake_target = ""
        self._cmake_targets = []
        self.is_release = False
        self._need_clean = False
        self.jobs = 8

        self.process.output.connect(self.handle_output)
        self.process.finished.connect(self.process_finished)
        self.process.failed.connect(self.process_failed)

    @pyqtSlot(tuple, object)
    def start_build(self, args: tuple, cancel_event: Event) -> None:
        self.compile_qt_project(*args, cancel_event=cancel_event)

    def compile_qt_project(
        self,
        project_file: str,
        qt_bin: str,
        compiler: Toolchain,
        output_path: str,
        external_sources: list[dict],
        is_release: bool,
        need_clean: bool,
        jobs: int = os.cpu_count() or 8,
        *,
        cancel_event: Event | None = None,
    ) -> None:
        """
        Build a qmake or CMake Qt project with MinGW or MSVC and deploy it.
        """
        if self._stage is not None:
            raise RuntimeError("A build is already running.")

        self._stage = Stage.PREPARATION
        self._cancel_event = cancel_event if cancel_event is not None else Event()
        try:
            self._check_cancelled()
            self.progress_signal.emit(BuildPhase.PREPARING)
            self.output_path = str(Path(output_path).resolve())
            self.qt_bin = str(Path(qt_bin).resolve())
            self.compiler = compiler
            project = Path(project_file).resolve(strict=True)
            if not project.is_file() or not (project.suffix.casefold() == ".pro" or is_cmake_project(project)):
                raise ValueError(f"Select an existing .pro or CMakeLists.txt file: {project}")
            self.project_file = str(project)
            self.external_sources = external_sources
            self.is_release = is_release
            self._need_clean = need_clean
            self._artifact = None
            self._publication_dir = None
            self._cmake = None
            self._cmake_target = ""
            self._cmake_targets = []
            self._msvc_env_output.clear()
            self._msvc_vcvars = None
            self.jobs = int(max(1, jobs))

            output_root = Path(self.output_path)
            output_root.mkdir(parents=True, exist_ok=True)
            self._build_root = Path(tempfile.mkdtemp(prefix=".qt-build-", dir=output_root))
            for entry in external_sources:
                self._check_cancelled()
                source = self._resource_source(entry)
                self._deployment_path(self._build_root, entry["Destination"])
                if entry.get("Type") in (QT_LIBRARY, QT_PLUGIN, QML_MODULE) and not source.is_relative_to(
                    Path(self.qt_bin).parent
                ):
                    raise ValueError(f"Qt dependency is outside the selected kit: {source}")
                if source.is_dir() and self._build_root.is_relative_to(source):
                    raise ValueError(f"Resource source contains the build directory: {source}")
            logger.info(
                f"project_file: {project_file}, output_path: {output_path}, qt_bin: {qt_bin}, compiler: {compiler}, external_sources: {external_sources}, is_release: {is_release}, need_clean: {need_clean}"
            )
            if self.compiler.kind == MSVC:
                base_env = os.environ.copy()
                base_env.pop("QMAKESPEC", None)
                self._environment = base_env
                cmd, command, vcvars = msvc_environment_command(self.compiler)
                self._msvc_vcvars = vcvars
                self._start_process(
                    Stage.PREPARATION,
                    cmd,
                    [],
                    message=tr("Setting up the MSVC environment...") + "\n",
                    cmdline=command,
                )
            else:
                self._environment = self._build_environment()
                self._check_cancelled()
                self._start_configure(project)
        except BuildCancelled as exc:
            self._finish(error=str(exc))
        except Exception as exc:
            self._finish(error=self._stage_error(exc))

    def _start_configure(self, project: Path) -> None:
        if is_cmake_project(project):
            self._start_cmake_configure(project)
        else:
            self._start_qmake(project)

    def _finalize_environment(self, environment: dict[str, str]) -> dict[str, str]:
        if self.compiler.kind == MSVC:
            tools = (self.qt_bin,)
            # Isolate MSVC's resident PDB server to this build to avoid cross-build interference.
            if self._build_root is not None:
                environment["_MSPDBSRV_ENDPOINT_"] = f"QtPackageTool-{os.getpid()}-{self._build_root.name}"
        else:
            tools = (str(self.compiler.directory), self.qt_bin)
        path_name = next((name for name in environment if name.casefold() == "path"), "PATH")
        environment[path_name] = os.pathsep.join(filter(None, (*tools, environment.get(path_name))))
        return environment

    def _build_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        # Use the selected Qt kit's default spec.
        environment.pop("QMAKESPEC", None)
        return self._finalize_environment(environment)

    def _environment_path(self) -> str:
        return next((value for name, value in self._environment.items() if name.casefold() == "path"), "")

    def _build_tool(self, name: str, *tool_dirs: str) -> Path | None:
        return find_build_tool(name, Path(self.qt_bin), tool_dirs, self._environment_path())

    def _make_command(self) -> tuple[Path, list[str]]:
        """Return the make tool and its parallel-job arguments for the selected compiler."""
        if self.compiler.kind != MSVC:
            return self.compiler.directory / "mingw32-make.exe", [f"-j{self.jobs}"]
        jom = self._build_tool("jom.exe", "QtCreator/bin/jom", "QtCreator/bin")
        if jom is not None:
            return jom, [f"-j{self.jobs}", "/NOLOGO"]
        nmake = self._build_tool("nmake.exe")
        if nmake is None:
            raise FileNotFoundError("nmake.exe was not found in the MSVC environment.")
        return nmake, ["/NOLOGO"]

    def _start_qmake(self, project: Path) -> None:
        args = qmake.configure_arguments(project, self._build_root, self.is_release)
        self._start_process(Stage.QMAKE, Path(self.qt_bin) / "qmake.exe", args)

    def _start_cmake_configure(self, project: Path) -> None:
        self._cmake = self._build_tool("cmake.exe", "CMake_64/bin", "CMake_32/bin")
        if self._cmake is None:
            raise FileNotFoundError("cmake.exe was not found. Install CMake with the Qt Maintenance Tool or add it to PATH.")
        build_dir = self._deployment_path(self._build_root, CMAKE_BUILD_DIR)
        ninja = self._build_tool("ninja.exe", "Ninja")
        args = cmake.configure_arguments(
            project, build_dir, Path(self.qt_bin), self.compiler, self.is_release, ninja
        )
        self._start_process(Stage.CMAKE_CONFIGURE, self._cmake, args)

    _cmake_relative_path = staticmethod(cmake.relative_path)

    _cmake_artifact_path = staticmethod(cmake.artifact_path)

    def _read_cmake_codemodel(self) -> tuple[Path, list[Path], list[Path]]:
        build_dir = self._deployment_path(self._build_root, CMAKE_BUILD_DIR)
        targets = cmake.read_codemodel(build_dir, self.is_release)
        self._cmake_target = targets.target
        self._cmake_targets = targets.build_targets
        return targets.executable, targets.shared_libraries, targets.module_libraries

    def _stage_cmake_package(self) -> Path:
        """Copy the built executable and the project's DLLs into a clean package folder."""
        cmake_build_dir = self._deployment_path(self._build_root, CMAKE_BUILD_DIR)
        executable, shared_libraries, module_libraries = self._read_cmake_codemodel()
        if not executable.is_file():
            raise FileNotFoundError(f"Executable not found: {executable}")
        package = self._deployment_path(self._build_root, CMAKE_PACKAGE_DIR)
        package.mkdir()
        self._copy_resource_file(executable, self._deployment_path(package, executable.name))
        for binary in shared_libraries:
            if binary.is_file():
                self._copy_resource_file(binary, self._deployment_path(package, binary.name))
        for binary in module_libraries:
            if not binary.is_file() or (binary.parent / "qmldir").is_file():
                continue
            # Keep plugin folders where they are relative to the executable.
            base = next(
                (root for root in (executable.parent, cmake_build_dir) if binary.is_relative_to(root)), None
            )
            if base is None:
                raise ValueError(f"Module library is outside the build and executable folders: {binary}")
            destination = self._deployment_path(package, binary.relative_to(base).as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._copy_resource_file(binary, destination)
        self._qml_import_paths = [str(cmake_build_dir)]
        self._resource_collections = []
        self._publication_dir = self._deployment_path(
            Path(self.output_path), "release" if self.is_release else "debug"
        )
        return Path(CMAKE_PACKAGE_DIR) / executable.name

    def _stage_error(self, error) -> str:
        return tr("{stage} failed: {error}").format(stage=tr(self._stage.value), error=error)

    def _start_process(
        self,
        stage: Stage,
        program: Path,
        args: list[str],
        message: str | None = None,
        *,
        cmdline: str | None = None,
    ) -> None:
        self._check_cancelled()
        self._stage = stage
        resolved = program.resolve()
        if resolved.parent != program.parent.resolve():
            raise ValueError(f"Build tool is outside the selected installation: {program}")
        program = resolved
        if not program.is_file():
            raise FileNotFoundError(f"{program.name} not found: {program}")
        logger.info(f"Running: {program} {args}")
        self.output_signal.emit(message or tr("Running {stage}...").format(stage=tr(stage.value)) + "\n")
        try:
            kwargs = {"encoding": "mbcs" if stage in (Stage.QMAKE, Stage.BUILD) else "utf-8"}
            if cmdline is not None:
                kwargs["cmdline"] = cmdline
            self.process.start(program, args, str(self._build_root), self._environment, **kwargs)
        except Exception as exc:
            raise RuntimeError(f"could not start {program.name}: {exc}") from exc

    @pyqtSlot(str, bool)
    def handle_output(self, text: str, is_stderr: bool) -> None:
        if self._stage == Stage.PREPARATION:
            if not is_stderr:
                self._msvc_env_output.append(text)
                return
        if self._stage in (Stage.QML_CHECK, Stage.QML_SOURCE_CHECK):
            if not is_stderr:
                self._qml_scan_output.append(text)
                return
            self._qml_scan_diagnostics.append(text)
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if is_stderr:
                if is_error_line(line):
                    logger.error(f"Compiler error: {stripped}")
                else:
                    logger.warning(stripped)
            else:
                logger.info(f"Compiler output: {stripped}")
            self.output_signal.emit(line)

    _read_qmake_file = staticmethod(qmake.read_qmake_file)

    def _makefile_target(self) -> tuple[Path, Path]:
        target = qmake.read_target(self._build_root, Path(self.output_path), self.is_release)
        self._qml_import_paths = target.import_paths
        self._resource_collections = target.resource_collections
        self._publication_dir = target.publication_dir
        return target.makefile, target.artifact

    @pyqtSlot(object)
    def process_finished(self, exit_code: int) -> None:
        stage = self._stage
        if stage is None:
            return
        if self._cancel_event.is_set():
            self._finish(error=BUILD_STOPPED_MESSAGE)
            return
        if exit_code:
            self._finish(error=tr("{stage} failed: exit code {code}").format(stage=tr(stage.value), code=exit_code))
            return

        try:
            self._advance(stage)
        except BuildCancelled as exc:
            self._finish(error=str(exc))
        except Exception as exc:
            self._finish(error=self._stage_error(exc))

    @pyqtSlot(str)
    def process_failed(self, message: str) -> None:
        if self._stage is not None:
            retained = tr("Build files retained at {path}.").format(path=self._build_root)
            self._finish(error=f"{self._stage_error(message)}\n{retained}", cleanup=False)

    def _advance(self, stage: Stage) -> None:
        """Start the step that follows a finished build tool."""
        self._check_cancelled()
        if stage == Stage.PREPARATION:
            configured = parse_msvc_environment("".join(self._msvc_env_output), self._msvc_vcvars)
            self._msvc_env_output.clear()
            self._environment = self._finalize_environment(configured)
            self._start_configure(Path(self.project_file))
            return
        if stage == Stage.QMAKE:
            makefile, self._artifact = self._makefile_target()
            make_tool, make_args = self._make_command()
            self.progress_signal.emit(BuildPhase.COMPILING)
            self._start_process(Stage.BUILD, make_tool, ["-f", makefile.name, *make_args])
            return
        if stage == Stage.CMAKE_CONFIGURE:
            self._read_cmake_codemodel()
            self.progress_signal.emit(BuildPhase.COMPILING)
            build_dir = self._deployment_path(self._build_root, CMAKE_BUILD_DIR)
            self._start_process(
                Stage.BUILD, self._cmake,
                ["--build", str(build_dir), "--target", *self._cmake_targets, "--parallel", str(self.jobs)],
            )
            return

        if stage == Stage.BUILD and self._cmake is not None:
            self._artifact = self._stage_cmake_package()
        exe_path = self._deployment_path(self._build_root, self._artifact.as_posix())
        build_dir = exe_path.parent
        if stage == Stage.BUILD:
            if not exe_path.is_file():
                raise FileNotFoundError(f"Executable not found: {exe_path}")
            self.progress_signal.emit(BuildPhase.DEPLOYING)
            project_dir = Path(self.project_file).parent
            self._qml_resource_sources = self._read_qml_resource_sources(project_dir)
            args = self._prepare_manual_dependencies(exe_path)
            qml_dirs = [self._copy_qml_sources(project_dir)]
            if self._manual_qml_file is not None:
                qml_dirs.append(self._manual_qml_file.parent)
            for directory in filter(None, qml_dirs):
                args.extend(("--qmldir", str(directory)))
            for directory in self._qml_import_dirs(project_dir):
                args.extend(("--qmlimport", directory))
            self._start_process(Stage.DEPLOY, Path(self.qt_bin) / "windeployqt.exe", args)
            return

        if stage == Stage.DEPLOY:
            source_copy = self._deployment_path(self._build_root, QML_SOURCE_COPY)
            if source_copy.is_dir():
                shutil.rmtree(source_copy, onerror=self._remove_readonly)
            external_files = [
                item for item in self.external_sources
                if item.get("Type") not in (QT_LIBRARY, QT_PLUGIN, QML_MODULE)
            ]
            if external_files:
                self._stage = Stage.RESOURCE_COPY
                self.output_signal.emit(tr("Copying external sources...") + "\n")
                self.copy_sources_to_output(external_files, str(build_dir))

            self.progress_signal.emit(BuildPhase.VALIDATING)
            suffixes = ("", "d") if self.is_release else ("d", "")
            self._qml_runtime = next(
                (
                    (major, bool(suffix))
                    for major in (6, 5)
                    for suffix in suffixes
                    if (build_dir / f"Qt{major}Qml{suffix}.dll").is_file()
                ),
                None,
            )
            if self._qml_runtime:
                self._start_qml_check(build_dir)
                return
            if self._manual_qml_file is not None:
                raise ValueError("The QML runtime required by the selected modules was not deployed.")

        if stage in (Stage.QML_CHECK, Stage.QML_SOURCE_CHECK):
            for diagnostic in "".join(self._qml_scan_diagnostics).splitlines():
                if diagnostic.startswith("Cannot open input file ") or re.match(
                    r".+\.(?:qml|m?js):\d+:", diagnostic, flags=re.IGNORECASE
                ):
                    raise ValueError(f"Could not read QML source ({diagnostic})")
            self._qml_scan_diagnostics.clear()
            modules = read_qml_modules("".join(self._qml_scan_output))
            self._qml_scan_output.clear()
            if stage == Stage.QML_CHECK:
                self._optional_qml_imports, self._qml_plugins = check_qml_modules(
                    modules, build_dir, self._qml_runtime[1], self._check_cancelled
                )
                if self._optional_qml_imports:
                    self._start_qml_check(build_dir, source_only=True)
                    return
            else:
                missing = self._optional_qml_imports & {module["name"] for module in modules}
                if missing:
                    raise ValueError(f"Required QML modules are unavailable: {', '.join(sorted(missing))}")
            self._deployment_path(self._build_root, ".qt-qml-check.qrc").unlink(missing_ok=True)
            if self._manual_qml_file is not None:
                self._manual_qml_file.unlink()
                self._manual_qml_file.parent.rmdir()

        self._stage = Stage.VALIDATION
        selected_libraries = (
            self._deployment_path(build_dir, entry["Destination"])
            for entry in self.external_sources if entry.get("Type") in (QT_LIBRARY, QT_PLUGIN)
        )
        validate_startup_libraries(
            exe_path, self._check_cancelled, sorted(self._qml_plugins), selected_libraries
        )
        if build_dir == self._build_root:
            if self._need_clean:
                self.clean_build_files(".qt-intermediates")
                for entry in list(build_dir.iterdir()):
                    if is_qmake_intermediate(entry.name) and self._entry_status(entry) and entry.is_file():
                        entry.unlink()
            else:
                package = self._deployment_path(self._build_root, CMAKE_PACKAGE_DIR)
                package.mkdir()
                for entry in list(self._build_root.iterdir()):
                    if entry == package or is_qmake_intermediate(entry.name):
                        continue
                    self._entry_status(entry)
                    entry.rename(self._deployment_path(package, entry.name))
                build_dir = package
        self._stage = Stage.PUBLISHING
        self.progress_signal.emit(BuildPhase.PUBLISHING)
        self._finish(self._publish(build_dir))

    def _prepare_manual_dependencies(self, executable: Path) -> list[str]:
        binary_entries = [
            entry for entry in self.external_sources if entry.get("Type") in (QT_LIBRARY, QT_PLUGIN)
        ]
        manual_imports = {
            entry["Import"] for entry in self.external_sources if entry.get("Type") == QML_MODULE
        }
        if not (binary_entries or manual_imports):
            return [str(executable)]

        self._stage = Stage.QT_STAGING
        qt_bin = Path(self.qt_bin)
        major = qt_major_version(qt_bin)
        debug = qt_debug_runtime(qt_bin, self.is_release)
        core = runtime_binary(qt_bin / f"Qt{major}Core.dll", debug)
        self.copy_sources_to_output(
            [entry for entry in binary_entries if Path(entry["Source"]) != core],
            str(executable.parent),
        )
        binaries = [str(executable), *(entry["Source"] for entry in binary_entries)]
        if manual_imports:
            for spec in manual_imports:
                if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?: \d+\.\d+)?", spec):
                    raise ValueError(f"Invalid Qt QML import: {spec}")
            directory = self._deployment_path(self._build_root, ".qt-selected-qml")
            directory.mkdir()
            self._manual_qml_file = directory / "imports.qml"
            self._manual_qml_file.write_text(
                "".join(f"import {spec}\n" for spec in sorted(manual_imports)) + "\nQtObject {}\n",
                encoding="utf-8",
            )
            self._qml_resource_sources.add(self._manual_qml_file)
            # Qt 5 decides whether to scan QML from the primary binary.
            binaries.insert(0, str(runtime_binary(qt_bin / f"Qt{major}Quick.dll", debug)))
        return ["--dir", str(executable.parent), *dict.fromkeys(binaries)]

    def _start_qml_check(self, build_dir: Path, *, source_only: bool = False) -> None:
        project_dir = Path(self.project_file).parent
        args = self._qml_check_sources(project_dir)
        if not source_only:
            args.extend(("-importPath", *(str(path) for path in qml_runtime_dirs(build_dir, self._qml_runtime[0]))))
        self._start_process(
            Stage.QML_SOURCE_CHECK if source_only else Stage.QML_CHECK,
            Path(self.qt_bin) / "qmlimportscanner.exe",
            args,
        )

    def _read_qml_resource_sources(self, project_dir: Path) -> set[Path]:
        return qml.read_resource_sources(project_dir, self._resource_collections, self._check_cancelled)

    def _qml_sources(self, project_dir: Path) -> list[Path]:
        if self._qml_source_list is None:
            self._qml_source_list = qml.list_sources(
                project_dir, Path(self.output_path), self._qml_resource_sources, self._check_cancelled
            )
        return self._qml_source_list

    def _copy_qml_sources(self, project_dir: Path) -> Path | None:
        return qml.copy_sources(
            self._qml_sources(project_dir), self._build_root, self._manual_qml_file, self._check_cancelled
        )

    def _qml_check_sources(self, project_dir: Path) -> list[str]:
        return qml.scan_arguments(self._qml_sources(project_dir), self._build_root)

    def _qml_import_dirs(self, project_dir: Path) -> list[str]:
        return qml.import_directories(project_dir, self._qml_import_paths, self._environment)

    def _finish(
        self, build_dir: Path | None = None, *, error: str | None = None, cleanup: bool = True
    ) -> None:
        if self._stage is None:
            return
        try:
            if (
                cleanup
                and self._build_root is not None
                and self._build_root.exists()
                and (error or self._need_clean)
            ):
                self.clean_build_files()
        except (OSError, ValueError) as exc:
            cleanup_error = tr("Could not clean temporary build files: {error}").format(error=exc)
            logger.warning(cleanup_error)
            if error:
                error = f"{error}\n{cleanup_error}"
            else:
                self.output_signal.emit(f"[Warning] {cleanup_error}\n")
        finally:
            self._build_root = None
            self._qml_resource_sources.clear()
            self._qml_source_list = None
            self._manual_qml_file = None
            self._qml_runtime = None
            self._qml_plugins.clear()
            self._qml_scan_output.clear()
            self._qml_scan_diagnostics.clear()
            self._optional_qml_imports.clear()
            self._msvc_env_output.clear()
            self._msvc_vcvars = None
            self._cmake_targets = []
            self._stage = None
            if self._shutting_down:
                self.thread().quit()

        if error:
            logger.error(error)
            self.error_signal.emit(error)
            return
        logger.info(f"Build successful to directory: {build_dir}")
        self.finished_signal.emit(build_dir)

    def _publish(self, build_dir: Path) -> Path:
        root = Path(self.output_path)
        target = self._deployment_path(root, self._publication_dir.relative_to(root).as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        for number in count(1):
            name = target.name if number == 1 else f"{target.name}-{number}"
            candidate = self._deployment_path(root, (target.parent / name).relative_to(root).as_posix())
            # Sync clients can keep new package files busy briefly after deployment.
            for delay in (0.05, 0.15, 0.3, 0.7, 0.9, None):
                self._check_cancelled()
                if self._entry_status(candidate) is not None:
                    break
                try:
                    return build_dir.rename(candidate)
                except FileExistsError:
                    # A concurrent build can claim the name after the check above.
                    break
                except OSError as exc:
                    if delay is None or getattr(exc, "winerror", None) not in (
                        winerror.ERROR_ACCESS_DENIED,
                        winerror.ERROR_SHARING_VIOLATION,
                        winerror.ERROR_LOCK_VIOLATION,
                    ):
                        raise
                    self._cancel_event.wait(delay)

    _entry_status = staticmethod(entry_status)

    _deployment_path = staticmethod(deployment_path)

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise BuildCancelled(BUILD_STOPPED_MESSAGE)

    def _copy_resource_file(self, source, destination) -> None:
        self._check_cancelled()
        source, destination = Path(source), Path(destination)
        self._entry_status(source)
        if self._entry_status(destination) is not None:
            if destination.is_file() and filecmp.cmp(source, destination, shallow=False):
                return
            raise FileExistsError(f"Resource destination already exists: {destination}")

        with source.open("rb") as input_file:
            output_file = destination.open("xb")
            try:
                with output_file:
                    while block := input_file.read(shutil.COPY_BUFSIZE):
                        self._check_cancelled()
                        output_file.write(block)
                shutil.copystat(source, destination)
            except (OSError, BuildCancelled):
                destination.unlink(missing_ok=True)
                raise

    def _copy_resource_directory(self, source: Path, destination: Path) -> None:
        def check_children(directory, names):
            self._check_cancelled()
            source_dir = Path(directory)
            target_dir = destination / source_dir.relative_to(source)
            self._entry_status(target_dir)
            for name in names:
                self._entry_status(source_dir / name)
                self._deployment_path(target_dir, name)
            return []

        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=check_children,
            copy_function=self._copy_resource_file,
        )

    _resource_source = staticmethod(resource_source)

    def copy_sources_to_output(self, external_source, output_dir: str) -> None:
        """
        Copy source files and folders to the output directory based on the external source.
        """
        package_dir = Path(output_dir)

        for entry in external_source:
            self._check_cancelled()
            src = entry.get("Source")
            dest_rel = entry.get("Destination")
            logger.info(f"Try to copy {src} to {dest_rel}")

            try:
                source = self._resource_source(entry)
                dest = self._deployment_path(package_dir, dest_rel)
                if source.is_dir() and (
                    source.is_relative_to(package_dir) or package_dir.is_relative_to(source)
                ):
                    raise ValueError(f"Resource source overlaps the package: {source}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    self._copy_resource_directory(source, dest)
                else:
                    self._copy_resource_file(source, dest)
                logger.info(f"Copied {src} -> {dest}")
                self.output_signal.emit(tr("Copied {source} -> {destination}").format(source=src, destination=dest))

            except (OSError, ValueError) as e:
                raise OSError(f"Failed to copy {src} to {dest_rel}: {e}") from e

        logger.info("All external sources copied successfully.")

    def clean_build_files(self, subdirectory: str | None = None) -> None:
        """Remove only the temporary compiler output for this build."""
        directory = self._deployment_path(Path(self.output_path), self._build_root.name)
        if directory != self._build_root:
            raise ValueError(f"Unexpected intermediate directory: {directory}")
        if subdirectory is not None:
            directory = self._deployment_path(directory, subdirectory)
        if directory.exists():
            shutil.rmtree(directory, onerror=self._remove_readonly)
        logger.info(f"Cleaned temporary build directory: {directory}")
        self.output_signal.emit(tr("Cleaned temporary build files.") + "\n")

    _remove_readonly = staticmethod(remove_readonly)

    @pyqtSlot()
    def stop_process(self) -> bool:
        """Request cancellation and let the process runner confirm exit."""
        if self._stage is None:
            return False
        self._cancel_event.set()
        if self.process.running:
            self.process.terminate()
        else:
            self._finish(error=BUILD_STOPPED_MESSAGE)
        return True

    @pyqtSlot()
    def shutdown(self) -> None:
        self._shutting_down = True
        if self._stage is None:
            self.thread().quit()
        else:
            self.stop_process()
