# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QLocale, QSettings, pyqtSignal
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import app.controller as controller
from app.installer import launch_installer
from app.preferences import AppPreferences


class FakeUpdates(QObject):
    state_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.state = "idle"
        self.release = None
        self.installer = None
        self.checks = 0
        self.downloads = 0
        self.download_kind = "full"
        self.fallbacks = 0

    def check(self):
        self.checks += 1

    def download(self):
        self.downloads += 1
        self.state = "downloading"

    def cancel(self):
        pass

    def verified_installer(self):
        return self.installer

    def fallback_to_full(self):
        self.fallbacks += 1
        self.download_kind = "full"
        self.state = "downloading"
        return True


class UpdateIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        storage = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.preferences = AppPreferences(ROOT / "resource", storage, QLocale("en"))
        self.updates = FakeUpdates()
        with patch.object(controller.QtPackage, "scan_qt_path"), patch.object(controller, "IS_COMPILED", False):
            self.window = controller.QtPackage(self.preferences, self.updates)
        self.addCleanup(self.window._shutdown_compiler)
        self.addCleanup(self.window.ui.close)

    def test_source_launch_does_not_check_or_execute_an_installer(self):
        with patch.object(controller, "IS_COMPILED", False), patch.object(controller, "launch_installer") as launch:
            self.window._automatic_update_check()
            self.window._install_update()
        self.assertEqual(self.updates.checks, 0)
        launch.assert_not_called()

    def test_automatic_check_is_throttled_and_download_preference_is_saved(self):
        with patch.object(controller, "IS_COMPILED", True):
            self.window._automatic_update_check()
            self.assertEqual(self.updates.checks, 1)
            self.preferences.mark_update_checked()
            self.window._automatic_update_check()
            self.assertEqual(self.updates.checks, 1)
            self.updates.state = "available"
            self.preferences.select_auto_download_updates(False)
            self.assertEqual(self.updates.downloads, 0)
            self.preferences.select_auto_download_updates(True)
            self.assertEqual(self.updates.downloads, 1)
        reopened = AppPreferences(
            ROOT / "resource",
            QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat), QLocale("en"),
        )
        self.assertTrue(reopened.auto_download_updates)
        self.assertFalse(reopened.update_check_due())

    def test_running_build_prevents_installation_without_stopping_build(self):
        self.window._set_packaging_state(True)
        with (
            patch.object(controller, "IS_COMPILED", True),
            patch.object(controller, "launch_installer") as launch,
            patch.object(self.window, "_stop_build") as stop,
        ):
            self.window._install_update()
        self.assertTrue(self.window.is_compiling)
        launch.assert_not_called()
        stop.assert_not_called()
        self.window._set_packaging_state(False)

    def test_pending_update_is_checked_again_after_cancel_or_restart(self):
        self.preferences.mark_update_checked()
        self.assertFalse(self.preferences.update_check_due())
        self.updates.release = SimpleNamespace(version="1.0.1", notes="New release")
        self.updates.state = "available"
        with patch.object(controller, "IS_COMPILED", False):
            self.window._update_state_changed("available")
        reopened = AppPreferences(
            ROOT / "resource",
            QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat), QLocale("en"),
        )
        self.assertTrue(reopened.update_check_due())

    def test_install_saves_session_and_only_closes_after_successful_handoff(self):
        self.updates.installer = self.root / "QtPackageTool-Setup-x64.exe"
        with (
            patch.object(controller, "IS_COMPILED", True),
            patch.object(controller, "launch_installer", return_value=False),
            patch.object(self.window.ui, "close") as close,
        ):
            self.window._install_update()
            close.assert_not_called()
        with (
            patch.object(controller, "IS_COMPILED", True),
            patch.object(controller, "launch_installer", return_value=True) as launch,
            patch.object(self.window.ui, "close") as close,
        ):
            self.window._install_update()
            launch.assert_called_once_with(self.updates.installer, controller.APP_DIR, os.getpid())
            close.assert_called_once()
        self.assertIn("release", self.preferences.project_session())

    def test_installer_receives_paths_as_arguments_without_shell_expansion(self):
        installer = self.root / "中文 & update" / "setup.exe"
        app_dir = self.root / "app & folder"
        with patch("app.installer.QProcess.startDetached", return_value=(True, 123)) as start:
            self.assertTrue(launch_installer(installer, app_dir, 42))
        program, arguments, cwd = start.call_args.args
        self.assertEqual(program, str(installer))
        self.assertEqual(cwd, str(installer.parent))
        self.assertIn(f"/DIR={app_dir}", arguments)
        self.assertIn("/WAITPID=42", arguments)
        self.assertIn("/RESTARTAPP=1", arguments)

    def test_delta_handoff_preparation_failure_downloads_full_without_closing(self):
        self.updates.download_kind = "delta"
        self.updates.installer = self.root / "delta.zip"
        self.updates.release = SimpleNamespace(version="1.0.1")
        with (
            patch.object(controller, "IS_COMPILED", True),
            patch.object(controller, "launch_delta_update", side_effect=ValueError("Invalid local helper")),
            patch.object(controller, "launch_installer") as full,
            patch.object(self.window.ui, "close") as close,
        ):
            self.window._install_update()
            full.assert_not_called()
            close.assert_not_called()
        self.assertEqual(self.updates.fallbacks, 1)
        self.assertEqual(self.updates.state, "downloading")
        self.assertFalse(self.window._installing_update)

    def test_delta_successful_handoff_saves_session_then_closes(self):
        self.updates.download_kind = "delta"
        self.updates.installer = self.root / "delta.zip"
        self.updates.release = SimpleNamespace(version="1.0.1")
        with (
            patch.object(controller, "IS_COMPILED", True),
            patch.object(controller, "launch_delta_update", return_value=True) as launch,
            patch.object(self.window.ui, "close") as close,
        ):
            self.window._install_update()
            launch.assert_called_once_with(
                self.updates.installer, self.updates.release, controller.APP_DIR, os.getpid(),
            )
            close.assert_called_once()
        self.assertTrue(self.window._installing_update)
