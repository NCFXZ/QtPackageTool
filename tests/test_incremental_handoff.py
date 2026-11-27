# SPDX-License-Identifier: GPL-3.0-or-later

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app import bootstrap
from app.installer import launch_delta_update
from app.metadata import APP_VERSION, FILE_MANIFEST_NAME, UPDATER_NAME


class IncrementalHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app_dir = self.root / "中文 & app"
        self.work = self.root / "download"
        self.app_dir.mkdir()
        self.work.mkdir()
        self.helper_bytes = b"verified standalone updater executable"
        (self.app_dir / UPDATER_NAME).write_bytes(self.helper_bytes)
        self.manifest = {
            "schema": 1, "version": APP_VERSION,
            "files": [{"path": UPDATER_NAME, "size": len(self.helper_bytes),
                       "sha256": hashlib.sha256(self.helper_bytes).hexdigest()}],
        }
        (self.app_dir / FILE_MANIFEST_NAME).write_text(json.dumps(self.manifest), encoding="utf-8")
        self.archive = self.work / "delta.zip"
        self.archive.write_bytes(b"verified delta")
        self.release = SimpleNamespace(
            version="1.0.1", url="https://github.com/NCFXZ/QtPackageTool/releases/download/v1.0.1/QtPackageTool-Setup-x64.exe",
            size=456, sha256="a" * 64,
            delta=SimpleNamespace(size=self.archive.stat().st_size, sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest()),
            file_manifest=SimpleNamespace(
                url="https://github.com/NCFXZ/QtPackageTool/releases/download/v1.0.1/QtPackageTool-files.json",
                size=123, sha256="b" * 64,
            ),
        )

    def test_handoff_uses_a_verified_independent_helper_and_structured_plan(self):
        with patch("app.installer.QProcess.startDetached", return_value=(True, 123)) as start:
            self.assertTrue(launch_delta_update(self.archive, self.release, self.app_dir, 42))
        program, arguments, cwd = start.call_args.args
        self.assertEqual(Path(program).parent, self.work)
        self.assertNotEqual(Path(program).parent, self.app_dir)
        self.assertEqual(Path(program).read_bytes(), self.helper_bytes)
        self.assertEqual(arguments[0], "--plan")
        self.assertEqual(cwd, str(self.work))
        plan = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
        self.assertEqual(plan["app_dir"], str(self.app_dir))
        self.assertEqual(plan["wait_pid"], 42)
        self.assertEqual(plan["base_version"], APP_VERSION)
        self.assertEqual(plan["target_version"], "1.0.1")
        self.assertEqual(plan["full"]["sha256"], self.release.sha256)
        self.assertEqual(plan["delta"]["path"], str(self.archive))
        self.assertEqual(plan["manifest"]["sha256"], self.release.file_manifest.sha256)

    def test_changed_helper_is_rejected_before_any_process_is_started(self):
        (self.app_dir / UPDATER_NAME).write_bytes(b"changed updater")
        with patch("app.installer.QProcess.startDetached") as start:
            with self.assertRaises(ValueError):
                launch_delta_update(self.archive, self.release, self.app_dir, 42)
            start.assert_not_called()
        self.assertEqual(list(self.work.iterdir()), [self.archive])

    def test_deep_or_invalid_manifest_fails_with_a_handled_value_error(self):
        manifest = self.app_dir / FILE_MANIFEST_NAME
        for value in (
            "[" * 2000 + "0" + "]" * 2000,
            json.dumps({**self.manifest, "schema": True}),
        ):
            with self.subTest(value=value[:60]), patch("app.installer.QProcess.startDetached") as start:
                manifest.write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    launch_delta_update(self.archive, self.release, self.app_dir, 42)
                start.assert_not_called()

    def test_failed_process_creation_cleans_only_created_helper_files(self):
        unrelated = self.work / "user.txt"
        unrelated.write_text("preserve", encoding="utf-8")
        with patch("app.installer.QProcess.startDetached", return_value=(False, 0)):
            self.assertFalse(launch_delta_update(self.archive, self.release, self.app_dir, 42))
        self.assertEqual({path.name for path in self.work.iterdir()}, {self.archive.name, unrelated.name})

    def test_updater_lock_prevents_gui_start_and_releases_application_handle(self):
        application_mutex, updater_mutex = MagicMock(), MagicMock()
        with (
            patch.object(bootstrap, "configure_logging"),
            patch.object(bootstrap.win32event, "CreateMutex", return_value=application_mutex),
            patch.object(bootstrap.win32event, "OpenMutex", return_value=updater_mutex),
            patch("PyQt6.QtWidgets.QApplication") as application,
        ):
            self.assertEqual(bootstrap.run(), 0)
            application.assert_not_called()
        application_mutex.Close.assert_called_once()
        updater_mutex.Close.assert_called_once()
