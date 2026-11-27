# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QLocale, QSettings, Qt
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import app.controller as main_module
from app.controller import QtPackage, ScanFinishedEvent
from toolchains.discovery import Toolchain
from UI.ui_preferences import AppPreferences


def make_kit(root: Path, version: str, name: str, *dlls: str) -> tuple[Path, Toolchain]:
    kit_bin = root / version / name / "bin"
    kit_bin.mkdir(parents=True, exist_ok=True)
    for file in ("qmake.exe", "windeployqt.exe", *dlls):
        (kit_bin / file).touch()
    return kit_bin, Toolchain(kit_bin, "x86_64", "11.2", version)


class UiAndSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _create_window(self, folder: str, session: dict | None = None, cache: dict | None = None) -> QtPackage:
        ini = str(Path(folder) / "settings.ini")
        prefs = AppPreferences(ROOT / "resource", QSettings(ini, QSettings.Format.IniFormat), QLocale("en"))
        if session is not None:
            prefs.save_project_session(session)
        if cache is not None:
            prefs.save_toolchain_cache(cache)
        with patch.object(QtPackage, "scan_qt_path"):
            window = QtPackage(prefs)
        self.addCleanup(window._shutdown_compiler)
        self.addCleanup(window.ui.close)
        return window

    def test_info_bar_renders_plain_text_without_html_decoding_or_newline_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            window = self._create_window(folder)
            message = "<b>A & B</b>\nLine 2"
            bar = window._info_bar(main_module.InfoBarIcon.ERROR, "Failed", message, 1000)
            self.assertEqual(bar.contentLabel.textFormat(), Qt.TextFormat.PlainText)
            self.assertEqual(bar.titleLabel.textFormat(), Qt.TextFormat.PlainText)
            self.assertEqual(bar.contentLabel.text(), message)
            bar.close()

    def test_cached_refresh_preserves_pending_kit_and_dependencies_until_live_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            mingw = root / "mingw" / "bin"
            mingw.mkdir(parents=True)
            for name in ("g++.exe", "mingw32-make.exe"):
                (mingw / name).touch()
            compiler = Toolchain(mingw, "x86_64", "11.2")

            _, kit_a = make_kit(root / "Qt", "6.5.3", "mingw_64", "Qt6Core.dll")
            kit_b_bin, kit_b = make_kit(root / "Qt", "6.6.0", "mingw_64", "Qt6Core.dll", "Qt6Svg.dll")

            window = self._create_window(
                folder,
                session={"kit": str(kit_b_bin), "dependencies": ["QtSvg"]},
            )
            # First refresh comes from a stale cache containing only Kit A.
            window.refresh_qt_kits({"Qt 6.5.3 (mingw_64)": kit_a}, [compiler], from_cache=True)
            self.assertEqual(window.ui.qt_package_settings.kit_combo.currentIndex(), -1)
            self.assertEqual(window._pending_session.get("kit"), str(kit_b_bin))
            self.assertEqual(window._pending_session.get("dependencies"), ["QtSvg"])

            # Live scan finds Kit B and restores both kit and dependencies.
            window.refresh_qt_kits(
                {"Qt 6.5.3 (mingw_64)": kit_a, "Qt 6.6.0 (mingw_64)": kit_b},
                [compiler],
            )
            pair = window.ui.qt_package_settings.kit_combo.currentData()
            self.assertIsNotNone(pair)
            self.assertEqual(pair[0].directory, kit_b_bin)
            self.assertEqual([dep.name for dep in window.ui.qt_package_settings.dependencies()], ["QtSvg"])

    def test_scan_is_deferred_while_compiling_and_language_is_locked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            mingw = root / "mingw" / "bin"
            mingw.mkdir(parents=True)
            for name in ("g++.exe", "mingw32-make.exe"):
                (mingw / name).touch()
            compiler = Toolchain(mingw, "x86_64", "11.2")
            _, kit_a = make_kit(root / "Qt", "6.5.3", "mingw_64", "Qt6Core.dll")
            _, kit_b = make_kit(root / "Qt", "6.6.0", "mingw_64", "Qt6Core.dll")

            window = self._create_window(folder)
            window.refresh_qt_kits({"Qt 6.5.3 (mingw_64)": kit_a}, [compiler], from_cache=True)
            window._set_packaging_state(True)
            self.assertFalse(window.ui.preferences_ui.language_combo.isEnabled())

            event = ScanFinishedEvent(window._scan_id, {"Qt 6.6.0 (mingw_64)": kit_b}, [compiler])
            window.event(event)
            # Still showing Kit A during the active build.
            self.assertEqual(window.ui.qt_package_settings.kit_combo.currentText(), "Qt 6.5.3 (mingw_64)")

            window._set_packaging_state(False)
            self.assertTrue(window.ui.preferences_ui.language_combo.isEnabled())
            self.assertEqual(window.ui.qt_package_settings.kit_combo.currentText(), "Qt 6.6.0 (mingw_64)")

    def test_external_paths_handle_trailing_slash_dedup_and_malformed_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data_dir = root / "assets"
            data_dir.mkdir()
            sample = root / "Config.INI"
            sample.write_text("k=v\n", encoding="utf-8")

            window = self._create_window(
                folder,
                session={"project": 123, "output": ["bad"], "external": [[0, 1], "bad"], "dependencies": "bad"},
            )
            window.add_external_path_to_table(str(data_dir) + "/")
            self.assertEqual(window.ui.qt_package_settings.external_rows(), [(str(data_dir) + "/", "/assets")])

            # Same file with different slashes and case should be deduplicated.
            window.add_external_path_to_table(str(sample).replace("\\", "/"))
            window.add_external_path_to_table(str(sample).lower(), notify=False)
            self.assertEqual(len(window.ui.qt_package_settings.external_rows()), 2)

            # Drive root should be rejected rather than added as "/".
            window.add_external_path_to_table(root.anchor)
            self.assertEqual(len(window.ui.qt_package_settings.external_rows()), 2)

            # open_build_folder uses the explicit "explore" verb.
            window.build_path = data_dir
            with patch.object(main_module.os, "startfile") as startfile:
                window.open_build_folder()
            startfile.assert_called_once_with(data_dir, "explore")

    def test_handles_missing_primary_screen_and_missing_kit_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            with patch.object(QApplication, "primaryScreen", return_value=None):
                window = self._create_window(folder)
            self.assertEqual(window.ui.height(), 800)

            unversioned_bin = root / "custom_kit" / "bin"
            unversioned_bin.mkdir(parents=True)
            combo = window.ui.qt_package_settings.kit_combo
            combo.addItem("Custom", userData=(Toolchain(unversioned_bin, "x86_64"), Toolchain(unversioned_bin, "x86_64")))
            combo.setCurrentIndex(0)
            window.ui.qt_package_settings.build_combo_box.setCurrentIndex(1)  # Debug
            self.assertFalse(window._dependency_runtime_is_debug())


if __name__ == "__main__":
    unittest.main()
