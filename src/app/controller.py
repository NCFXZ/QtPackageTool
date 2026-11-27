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
import threading
import json
from pathlib import Path
from loguru import logger

from PyQt6.QtCore import pyqtSignal, pyqtSlot, QEvent, QSignalBlocker, QThread, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QWidget,
)

from qfluentwidgets import InfoBar, InfoBarIcon, PushButton

from app.runtime import APP_DIR, IS_COMPILED, RESOURCE_DIR
from app.installer import launch_delta_update, launch_installer
from app.updates import UpdateService
from UI.ui_main import MainUI
from UI.ui_notifications import WindowNotifications
from app.localization import tr
from build.compiler import BUILD_STOPPED_MESSAGE, BuildPhase, QtCompiler, is_cmake_project, is_error_line
from deployment.qt_dependencies import discover_qt_dependencies, qt_debug_runtime
from toolchains.discovery import discover_toolchains, matching_compiler, toolchains_from_data, toolchains_to_data


BUILD_PHASES = tuple(BuildPhase)
INFO_BARS = {
    1: (InfoBarIcon.SUCCESS, "Success"),
    0: (InfoBarIcon.ERROR, "Failed"),
    -1: (InfoBarIcon.INFORMATION, "Notification"),
}


class QtPackage(QWidget):
    emit_operation_status = pyqtSignal(int, str, int)
    _build_requested = pyqtSignal(tuple, object)
    _cancel_requested = pyqtSignal()
    _shutdown_requested = pyqtSignal()

    def __init__(self, preferences=None, update_service=None):
        """
        Initialize the QtPackage class.
        """
        logger.info("Program started")

        super().__init__()

        self.ui = MainUI(preferences)
        self.compiler = QtCompiler()
        self._build_thread = QThread(self)
        self.compiler.moveToThread(self._build_thread)
        self._build_thread.finished.connect(self.compiler.deleteLater)
        self._build_cancel = None
        self._close_after_build = False
        self.notifications = WindowNotifications(self.ui)
        self.updates = update_service if update_service is not None else UpdateService(
            self, installed_dir=APP_DIR if IS_COMPILED else None,
        )
        self._installing_update = False
        self._update_message = ""

        self.qt_dependencies = {}
        self._dependency_catalogs = {}
        self._selected_kit = None
        self.build_path: Path = None
        self.qt_project_file_path = ""
        self.qt_project_output_path = ""
        self.is_compiling = False
        self._scan_id = 0
        self._deferred_scan = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(35000)
        self._scan_timer.timeout.connect(self._scan_timed_out)

        self._pending_session = self.ui.preferences.project_session()

        self.set_connection()
        self.restore_session()
        self.ui.installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self._shutdown_compiler)
        self._build_thread.start()
        self.load_qt_dependencies()
        cached = toolchains_from_data(self.ui.preferences.toolchain_cache())
        if cached[0]:
            self.refresh_qt_kits(*cached, from_cache=True)
        self.scan_qt_path()
        self._connect_updates()
        if IS_COMPILED:
            QTimer.singleShot(2000, self._automatic_update_check)

        logger.info("Program initialized")

    def _connect_updates(self) -> None:
        page = self.ui.about_ui
        page.update_check_requested.connect(self.updates.check)
        page.update_download_requested.connect(self.updates.download)
        page.update_cancel_requested.connect(self.updates.cancel)
        page.update_install_requested.connect(self._install_update)
        self.updates.state_changed.connect(self._update_state_changed)
        self.updates.progress_changed.connect(page.set_update_progress)
        self.updates.error_occurred.connect(self._update_failed)
        self.ui.preferences.auto_download_updates_changed.connect(self._auto_download_changed)
        QApplication.instance().aboutToQuit.connect(self.updates.cancel)
        self._update_state_changed(self.updates.state)

    def _automatic_update_check(self) -> None:
        if IS_COMPILED and self.ui.preferences.update_check_due():
            self.updates.check()

    def _auto_download_changed(self, enabled: bool) -> None:
        if enabled and IS_COMPILED and self.updates.state == "available":
            self.updates.download()

    def _update_state_changed(self, state: str) -> None:
        if state != "error":
            self._update_message = ""
        release = self.updates.release
        self.ui.about_ui.set_update_state(
            state,
            version=release.version if release else "",
            notes=release.notes if release else "",
            message=self._update_message,
        )
        self.ui.about_ui.set_update_install_allowed(
            IS_COMPILED and not self.is_compiling and not self._installing_update
        )
        if state == "current":
            self.ui.preferences.mark_update_checked()
        elif state == "available":
            self.ui.preferences.mark_update_pending()
        if state == "available" and IS_COMPILED and self.ui.preferences.auto_download_updates:
            QTimer.singleShot(0, lambda: self._auto_download_changed(True))

    def _update_failed(self, message: str) -> None:
        self._update_message = message
        self._update_state_changed("error")
        logger.warning(f"Application update failed: {message}")

    def _install_update(self) -> None:
        if not IS_COMPILED:
            self.info_bar(-1, tr("Install updates from the installed application."))
            return
        if self.is_compiling or self._installing_update:
            self.info_bar(-1, tr("Wait for packaging to finish before installing an update."))
            return
        delta = False
        try:
            installer = self.updates.verified_installer()
            if installer is None:
                return
            self.save_session()
            delta = self.updates.download_kind == "delta"
            started = (
                launch_delta_update(installer, self.updates.release, APP_DIR, os.getpid()) if delta
                else launch_installer(installer, APP_DIR, os.getpid())
            )
            if not started:
                raise OSError(tr("Could not start the update installer."))
        except (OSError, ValueError) as exc:
            if delta:
                logger.warning(f"Incremental update handoff failed; downloading full installer: {exc}")
                self.updates.fallback_to_full()
                return
            self.info_bar(0, str(exc), 5000)
            return
        self._installing_update = True
        self.ui.close()

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
        self.ui.qt_package_settings.project_dropped.connect(self.set_project_file)
        self.ui.qt_package_settings.external_table.paths_dropped.connect(self.include_external_paths)
        self.ui.qt_package_project.notify.connect(lambda state, message: self.info_bar(state, message))
        self.ui.qt_package_settings.kit_combo.currentIndexChanged.connect(self.qt_kit_changed)
        self.ui.preferences.language_changed.connect(self._update_kit_tooltip)

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
        self.ui.qt_package_settings.dependencies_import_button.clicked.connect(
            self.import_dependencies
        )
        dependencies = self.ui.qt_package_settings.dependencies_module_combo_box
        dependencies.currentTextChanged.connect(self._update_import_button)
        dependencies.returnPressed.connect(self.import_dependencies)
        self.ui.qt_package_settings.build_combo_box.currentIndexChanged.connect(self._update_dependency_paths)
        self.ui.qt_package_settings.build_combo_box.currentIndexChanged.connect(self._update_build_summary)

        # Compiler
        self.compiler.output_signal.connect(self.handle_output)
        self.compiler.progress_signal.connect(self.handle_progress)
        self.compiler.finished_signal.connect(self.handle_finished)
        self.compiler.error_signal.connect(self.handle_error)
        self._build_requested.connect(self.compiler.start_build)
        self._cancel_requested.connect(self.compiler.stop_process)
        self._shutdown_requested.connect(self.compiler.shutdown)

        logger.info("Connections established")

    def load_qt_dependencies(self) -> None:
        """
        Load the dependencies from the JSON configuration file.
        """
        try:
            config_path = RESOURCE_DIR / "config" / "qt_dependencies.json"
            with config_path.open(encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                raise ValueError("Expected an object in the Qt dependency configuration.")
            self.qt_dependencies = config
            logger.info("Qt dependency configuration loaded")
        except (OSError, ValueError) as exc:
            logger.warning(f"Could not load Qt dependencies: {exc}")
            self.qt_dependencies = {}

    def scan_qt_path(self, folder: str | None = None) -> None:
        logger.info(f"Scanning Qt installations: {folder or 'automatic discovery'}")
        self._scan_id += 1
        self.ui.qt_package_settings.set_kit_search_state(True)
        self._scan_timer.start()
        threading.Thread(
            target=self.scan_qt_path_thread, args=(folder, self._scan_id), daemon=True
        ).start()

    def _scan_timed_out(self) -> None:
        self._scan_id += 1
        settings = self.ui.qt_package_settings
        settings.set_kit_search_state(False, "Select a Qt kit" if settings.kit_combo.count() else "No compatible Qt kit")
        self.emit_operation_status.emit(-1, tr("Qt installation scan timed out."), 4000)

    def scan_qt_path_thread(self, folder: str | None, scan_id: int) -> None:
        try:
            event = ScanFinishedEvent(scan_id, *discover_toolchains(folder), automatic=folder is None)
        except Exception as exc:
            logger.warning(f"Qt installation scan failed: {exc}")
            event = ScanFinishedEvent(scan_id, {}, [], "Could not scan the Qt installation.")

        try:
            app = QApplication.instance()
            if app is not None:
                app.postEvent(self, event)
        except RuntimeError:
            return

    def refresh_qt_kits(self, qt_versions, compilers, *, from_cache: bool = False) -> None:
        """
        List the kits that have a compatible compiler, keeping the current selection when it is still available.
        """
        self._dependency_catalogs.clear()
        settings = self.ui.qt_package_settings
        combo = settings.kit_combo
        pending_kit = self._pending_session.get("kit")
        if not isinstance(pending_kit, str):
            pending_kit = ""
        current = str(self._selected_kit) if self._selected_kit else pending_kit
        with QSignalBlocker(combo):
            combo.clear()
            for label, kit in qt_versions.items():
                compiler = matching_compiler(kit, compilers)
                if compiler is not None:
                    combo.addItem(label, userData=(kit, compiler))
            if combo.count() > 1 or (from_cache and pending_kit):
                combo.setCurrentIndex(-1)
            for index in range(combo.count()):
                if current and str(combo.itemData(index)[0].directory) == current:
                    combo.setCurrentIndex(index)
        combo.setEnabled(bool(combo.count()))
        settings.set_kit_search_state(False, "Select a Qt kit" if combo.count() else "No compatible Qt kit")
        self.qt_kit_changed()
        pair = combo.currentData()
        matched_pending = bool(pair and pending_kit and str(pair[0].directory) == pending_kit)
        if matched_pending:
            self._pending_session.pop("kit", None)
            dependencies = self._pending_session.pop("dependencies", [])
            if isinstance(dependencies, list):
                self._restore_dependencies([name for name in dependencies if isinstance(name, str)])
        elif not from_cache:
            self._pending_session.pop("kit", None)
            self._pending_session.pop("dependencies", None)

    @pyqtSlot()
    def select_qt_folder(self) -> None:
        """
        Open a file dialog to select the Qt installation folder.
        """
        folder = QFileDialog.getExistingDirectory(
            self.ui,
            tr("Select Qt Installation Folder"),
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
            tr("Select Qt Project File"),
            "",
            tr("Qt Projects (*.pro CMakeLists.txt)"),
        )
        if file_path:
            self.set_project_file(file_path)

    @pyqtSlot(str)
    def set_project_file(self, file_path: str) -> None:
        project = Path(file_path)
        if not project.is_file() or not (project.suffix.casefold() == ".pro" or is_cmake_project(project)):
            self.emit_operation_status.emit(0, tr("Select an existing .pro or CMakeLists.txt file."), 5000)
            return
        logger.info(f"User selected project: {file_path}")
        self.ui.qt_package_settings.project_select_edit.setText(file_path)
        self.qt_project_file_path = file_path
        self._update_build_summary()

    @pyqtSlot()
    def qt_kit_changed(self) -> None:
        settings = self.ui.qt_package_settings
        pair = settings.kit_combo.currentData()
        directory = pair[0].directory if pair else None
        if directory != self._selected_kit:
            settings.dependencies_table.setRowCount(0)
            self._selected_kit = directory
        choices = {}
        if pair is not None:
            kit, compiler = pair
            logger.info(f"Selected Qt kit {kit.directory} with compiler {compiler.label} ({compiler.directory})")
            if kit.directory not in self._dependency_catalogs:
                try:
                    self._dependency_catalogs[kit.directory] = discover_qt_dependencies(
                        kit.directory, self.qt_dependencies
                    )
                except (OSError, ValueError) as exc:
                    logger.warning(f"Could not list Qt dependencies: {exc}")
            choices = self._dependency_catalogs.get(kit.directory, {})
        settings.set_dependency_choices(choices, kit_selected=pair is not None)
        self._update_import_button("")
        self._update_kit_tooltip()
        self._update_build_summary()

    def _update_kit_tooltip(self) -> None:
        combo = self.ui.qt_package_settings.kit_combo
        pair = combo.currentData()
        if pair is None:
            combo.setToolTip("")
            return
        compiler = tr("Matched compiler: {compiler}").format(compiler=pair[1].label)
        combo.setToolTip(f"{combo.currentText()}\n{compiler}")

    @pyqtSlot(str)
    def _update_import_button(self, text: str) -> None:
        settings = self.ui.qt_package_settings
        combo = settings.dependencies_module_combo_box
        settings.dependencies_import_button.setEnabled(combo.itemData(combo.findText(text)) is not None)

    @pyqtSlot()
    def import_dependencies(self) -> None:
        settings = self.ui.qt_package_settings
        combo = settings.dependencies_module_combo_box
        dependency = combo.itemData(combo.findText(combo.currentText()))
        if dependency is None:
            self.emit_operation_status.emit(-1, tr("Choose an installed Qt dependency."), 3000)
            return
        if not self._add_dependency(dependency):
            self.emit_operation_status.emit(-1, tr("Dependency already imported."), 2000)

    def _restore_dependencies(self, names: list) -> None:
        combo = self.ui.qt_package_settings.dependencies_module_combo_box
        available = {}
        for index in range(combo.count()):
            dependency = combo.itemData(index)
            if dependency is not None:
                available[dependency.name] = dependency
        for name in names:
            if name in available:
                self._add_dependency(available[name])

    def _add_dependency(self, dependency) -> bool:
        return self.ui.qt_package_settings.add_dependency_row(dependency, self._dependency_runtime_is_debug())

    def _dependency_runtime_is_debug(self) -> bool:
        settings = self.ui.qt_package_settings
        pair = settings.kit_combo.currentData()
        if not pair:
            return False
        try:
            return qt_debug_runtime(pair[0].directory, settings.build_combo_box.currentData())
        except (OSError, ValueError) as exc:
            logger.warning(f"Could not determine Qt runtime build mode for {pair[0].directory}: {exc}")
            return False

    @pyqtSlot()
    def _update_dependency_paths(self) -> None:
        self.ui.qt_package_settings.update_dependency_paths(self._dependency_runtime_is_debug())

    @pyqtSlot()
    def select_output_directory(self) -> None:
        """
        Open a dialog for the user to select a project output directory.
        """
        dir_path = QFileDialog.getExistingDirectory(
            self.ui,
            tr("Select Project Output Directory"),
            "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )

        if dir_path:
            self.set_output_directory(dir_path)

    def set_output_directory(self, dir_path: str) -> None:
        if not Path(dir_path).is_dir():
            self.emit_operation_status.emit(0, tr("Select an existing output folder."), 5000)
            return
        logger.info(f"User selected output directory: {dir_path}")
        self.ui.qt_package_settings.output_path_edit.setText(dir_path)
        self.qt_project_output_path = dir_path
        self._update_build_summary()

    @pyqtSlot()
    def _update_build_summary(self) -> None:
        settings = self.ui.qt_package_settings
        project = Path(self.qt_project_file_path)
        if self.qt_project_file_path and is_cmake_project(project):
            project_name = f"{project.parent.name}/{project.name}"
        else:
            project_name = project.name if self.qt_project_file_path else ""
        self.ui.qt_package_project.set_build_summary(
            project=project_name,
            kit=settings.kit_combo.currentText() if settings.kit_combo.currentData() else "",
            mode=settings.build_combo_box.currentText(),
            output=self.qt_project_output_path,
        )

    def save_session(self) -> None:
        """
        Remember the project setup for the next launch.
        """
        settings = self.ui.qt_package_settings
        pair = settings.kit_combo.currentData()
        self.ui.preferences.save_project_session({
            "project": self.qt_project_file_path,
            "output": self.qt_project_output_path,
            "release": settings.build_combo_box.currentData(),
            "clean": settings.clean_switch.isChecked(),
            "kit": str(pair[0].directory) if pair else self._pending_session.get("kit", ""),
            "dependencies": [dependency.name for dependency in settings.dependencies()]
            or self._pending_session.get("dependencies", []),
            "external": [list(row) for row in settings.external_rows()],
        })

    def restore_session(self) -> None:
        """
        Restore the last project setup, skipping paths that no longer exist.
        The kit and dependencies are restored when the toolchain scan finishes.
        """
        session = self._pending_session
        settings = self.ui.qt_package_settings
        if not isinstance(session.get("kit"), str):
            session.pop("kit", None)
        if not isinstance(session.get("dependencies"), list):
            session.pop("dependencies", None)
        else:
            session["dependencies"] = [item for item in session["dependencies"] if isinstance(item, str)]
        project = session.get("project")
        if isinstance(project, str) and project:
            try:
                if Path(project).is_file():
                    self.set_project_file(project)
            except (OSError, ValueError):
                pass
        output = session.get("output")
        if isinstance(output, str) and output:
            try:
                if Path(output).is_dir():
                    self.set_output_directory(output)
            except (OSError, ValueError):
                pass
        build_mode = settings.build_combo_box.findData(session.get("release", True))
        settings.build_combo_box.setCurrentIndex(max(build_mode, 0))
        settings.clean_switch.setChecked(bool(session.get("clean", False)))
        external = session.get("external")
        if isinstance(external, list):
            for entry in external:
                if (
                    isinstance(entry, list)
                    and len(entry) == 2
                    and isinstance(entry[0], str)
                    and isinstance(entry[1], str)
                    and entry[0]
                    and "\x00" not in entry[0]
                    and os.path.exists(entry[0])
                ):
                    self.add_external_path_to_table(*entry, notify=False)
        self._update_build_summary()

    @pyqtSlot()
    def packaging_toggle(self) -> None:
        """
        Start the packaging process using the selected Qt compiler and project file.
        """
        if self._installing_update:
            return
        if self.is_compiling:
            self._stop_build()
            return

        settings = self.ui.qt_package_settings
        page = self.ui.qt_package_project
        pair = settings.kit_combo.currentData()
        required = (
            (pair and pair[0].matches(pair[1]), "Select a compatible Qt kit.", settings.kit_combo),
            (self.qt_project_file_path, "No project file selected.", settings.project_button),
            (self.qt_project_output_path, "No output directory selected.", settings.output_path_button),
        )
        for value, warning, widget in required:
            if not value:
                self._report_missing_setting(tr(warning), widget)
                return

        kit, compiler = pair
        self.save_session()

        compiler_text = settings.kit_combo.currentText()
        try:
            page.set_packaging_progress(BuildPhase.PREPARING.value, (0, len(BUILD_PHASES)))
            logger.info(f"Starting packaging with {compiler_text}...")
            page.append_terminal(tr("Starting packaging with {kit}...").format(kit=compiler_text))
            external_sources = self.extract_table_data()
            is_release = settings.build_combo_box.currentData()
            need_clean = settings.clean_switch.isChecked()
            self._build_cancel = threading.Event()
            self._set_packaging_state(True)
            self._build_requested.emit(
                (
                    self.qt_project_file_path,
                    str(kit.directory),
                    compiler,
                    self.qt_project_output_path,
                    external_sources,
                    is_release,
                    need_clean,
                    self.ui.preferences.build_jobs,
                ),
                self._build_cancel,
            )
        except Exception as exc:
            logger.error(f"Error occurred while packaging: {exc}")
            self.handle_error(str(exc))

    def _report_missing_setting(self, message: str, widget: QWidget) -> None:
        bar = self._info_bar(InfoBarIcon.WARNING, tr("Setup Incomplete"), message, 5000)
        button = PushButton(tr("Go to Project Setup"), bar)

        def go_to_setting() -> None:
            bar.close()
            self.ui.switchTo(self.ui.qt_package_settings)
            self.ui.qt_package_settings.reveal(widget)

        button.clicked.connect(go_to_setting)
        bar.addWidget(button)
        bar.show()

    def _stop_build(self) -> None:
        if self._build_cancel is not None and not self._build_cancel.is_set():
            logger.info("Stopping packaging process...")
            self._build_cancel.set()
            self._cancel_requested.emit()
            page = self.ui.qt_package_project
            page.set_packaging_action("Stopping...")
            page.package_toggle_button.setEnabled(False)
            page.set_packaging_progress("Stopping...")

    def eventFilter(self, watched, event) -> bool:
        if watched is self.ui and event.type() == QEvent.Type.Close:
            if self.is_compiling:
                event.ignore()
                self._close_after_build = True
                self._stop_build()
                return True
            self.save_session()
        return super().eventFilter(watched, event)

    @pyqtSlot()
    def _shutdown_compiler(self) -> None:
        if self._build_cancel is not None:
            self._build_cancel.set()
        self._shutdown_requested.emit()
        self._build_thread.wait()

    def _set_packaging_state(self, running: bool) -> None:
        self.is_compiling = running
        page = self.ui.qt_package_project
        page.set_packaging_action("Stop Packaging" if running else "Start Packaging")
        page.package_toggle_button.setEnabled(True)
        self.ui.qt_package_settings.setDisabled(running)
        self.ui.preferences_ui.language_combo.setDisabled(running)
        self.ui.about_ui.set_update_install_allowed(
            IS_COMPILED and not running and not self._installing_update
        )
        page.package_folder_button.setEnabled(not running and self.build_path is not None)
        if running:
            self.ui.set_packaging_badge("attension")
        if not running:
            self._build_cancel = None
            if self._deferred_scan is not None:
                deferred, self._deferred_scan = self._deferred_scan, None
                self._apply_scan_result(deferred)
            if self._close_after_build:
                self._close_after_build = False
                QTimer.singleShot(0, self.ui.close)

    @pyqtSlot(object)
    def handle_progress(self, phase: BuildPhase) -> None:
        if self.is_compiling and not self._build_cancel.is_set():
            self.ui.qt_package_project.set_packaging_progress(
                phase.value, (BUILD_PHASES.index(phase), len(BUILD_PHASES))
            )

    @pyqtSlot(str)
    def handle_output(self, text: str) -> None:
        """
        Handle normal output messages.
        """
        color = "orange" if is_error_line(text) else None
        self.ui.qt_package_project.append_terminal(text, color)

    @pyqtSlot(Path)
    def handle_finished(self, build_dir: Path) -> None:
        """
        Handle compiler finished signal.
        """
        self.build_path = build_dir
        self._set_packaging_state(False)
        self.ui.set_packaging_badge("success")
        page = self.ui.qt_package_project
        page.set_packaging_progress("Packaging Finished", (len(BUILD_PHASES), len(BUILD_PHASES)))
        page.append_terminal(tr("Packaging finished!"), "green")
        title = tr("Packaging Finished")
        self.emit_operation_status.emit(1, title, 2000)
        self.highlight_taskbar()
        self.tray_notification(
            title, tr("The Qt project has been packaged successfully.")
        )

    @pyqtSlot(str)
    def handle_error(self, msg: str) -> None:
        """
        Handle error messages.
        """
        self._set_packaging_state(False)
        page = self.ui.qt_package_project
        message, _, details = msg.partition("\n")
        if message == BUILD_STOPPED_MESSAGE:
            self.ui.set_packaging_badge(None)
            page.set_packaging_progress("Packaging Stopped")
            page.append_terminal(tr("Packaging Stopped"))
            if details:
                page.append_terminal(details, "orange")
            return
        self.ui.set_packaging_badge("error")
        page.set_packaging_progress("Packaging Failed", failed=True)
        page.append_terminal(tr("[Error] {message}").format(message=msg), "red")
        self.emit_operation_status.emit(0, msg, 2000)

    @pyqtSlot()
    def open_build_folder(self) -> None:
        """
        Open the build folder in the file explorer.
        """
        if self.build_path is None or not self.build_path.is_dir():
            logger.warning(f"Folder does not exist: {self.build_path}")
            self.emit_operation_status.emit(
                0, tr("Build folder does not exist: {path}").format(path=self.build_path), 2000
            )
            return
        try:
            logger.info(f"Opening build folder: {self.build_path}")
            os.startfile(self.build_path, "explore")
        except OSError as exc:
            logger.warning(f"Could not open build folder {self.build_path}: {exc}")
            self.emit_operation_status.emit(0, tr("Could not open the build folder."), 2000)

    @pyqtSlot()
    def include_external_folder(self) -> None:
        """
        Include an external folder.
        """
        folder_path = QFileDialog.getExistingDirectory(self.ui, tr("Select a Folder"))
        if folder_path:
            logger.info(f"User selected external folder to include: {folder_path}")
            self.add_external_path_to_table(folder_path)

    @pyqtSlot()
    def include_external_files(self) -> None:
        """
        Include external files.
        """
        file_paths, _ = QFileDialog.getOpenFileNames(self.ui, tr("Select Files"))
        self.include_external_paths(file_paths)

    @pyqtSlot(list)
    def include_external_paths(self, paths: list) -> None:
        for path in paths:
            logger.info(f"User selected external path to include: {path}")
            self.add_external_path_to_table(path)

    def add_external_path_to_table(self, path: str, dest_path: str | None = None, *, notify: bool = True) -> None:
        """
        Add a file or folder path to the external resources table.
        """
        if not (path and (os.path.isfile(path) or os.path.isdir(path))):
            logger.warning(f"Invalid path provided for external resources: ({path})")
            self.emit_operation_status.emit(0, tr("Invalid Path"), 2000)
            return
        if dest_path is None:
            name = Path(path).name
            if not name:
                logger.warning(f"Invalid path provided for external resources: ({path})")
                self.emit_operation_status.emit(0, tr("Invalid Path"), 2000)
                return
            destination = "/" + name
        else:
            destination = dest_path
        if not self.ui.qt_package_settings.add_external_row(path, destination):
            logger.info(f"File or path '{path}' already in the table.")
            if notify:
                self.emit_operation_status.emit(-1, tr("File or Path Already Included"), 2000)

    def extract_table_data(self) -> list[dict]:
        """
        Collect the external files and the selected Qt dependencies for the compiler.
        """
        settings = self.ui.qt_package_settings
        data = [
            {"Source": source, "Destination": destination, "Type": "Copy Only"}
            for source, destination in settings.external_rows()
        ]
        debug = self._dependency_runtime_is_debug()
        dependencies = {}
        for dependency in settings.dependencies():
            for entry in dependency.build_entries(debug):
                dependencies[tuple(entry.values())] = entry
        data.extend(dependencies.values())

        logger.info(f"Extracted table data: {data}")
        return data

    def _info_bar(self, icon: InfoBarIcon, title: str, message: str, duration: int) -> InfoBar:
        return self.notifications.info_bar(icon, title, message, duration)

    @pyqtSlot(int, str, int)
    def info_bar(self, state: int, message: str, time: int = 2000):
        """
        Show a success (1), failure (0) or information (-1) message.
        """
        icon, title = INFO_BARS[state]
        self._info_bar(icon, tr(title), message, time).show()

    def tray_notification(self, title: str, message: str):
        """
        Show a tray notification.
        """
        self.notifications.tray_notification(title, message)

    def highlight_taskbar(self):
        """
        Highlight the taskbar icon.
        """
        self.notifications.highlight_taskbar()

    def _apply_scan_result(self, e) -> None:
        if e.error is None or not self.ui.qt_package_settings.kit_combo.count():
            self.refresh_qt_kits(e.qt_versions, e.compilers)
        else:
            self.ui.qt_package_settings.set_kit_search_state(False)
        if e.error:
            self.emit_operation_status.emit(-1, tr(e.error), 4000)

    def event(self, e):
        """
        Handle custom events.
        """
        if e.type() == ScanFinishedEvent.EVENT_TYPE:
            if e.scan_id == self._scan_id:
                self._scan_timer.stop()
                if e.automatic and e.error is None:
                    self.ui.preferences.save_toolchain_cache(toolchains_to_data(e.qt_versions, e.compilers))
                if self.is_compiling:
                    self._deferred_scan = e
                else:
                    self._apply_scan_result(e)
            return True
        return super().event(e)


class ScanFinishedEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, scan_id, qt_versions, compilers, error=None, *, automatic=False):
        super().__init__(ScanFinishedEvent.EVENT_TYPE)
        self.scan_id = scan_id
        self.qt_versions = qt_versions
        self.compilers = compilers
        self.error = error
        self.automatic = automatic
