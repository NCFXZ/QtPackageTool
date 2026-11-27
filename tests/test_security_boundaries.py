# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded adversarial inputs and real Windows process-tree cancellation."""

from codecs import getincrementaldecoder
import copy
import os
from pathlib import Path
import random
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QLocale, QSettings
from PyQt6.QtWidgets import QApplication
import win32api
import win32con
import win32process
import pywintypes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.preferences import AUTO_JOBS, AppPreferences
from app.updates.models import allowed_download_url, parse_release
from app.updates.service import UpdateService
from build.paths import deployment_path
from build.process import BuildProcess
import build.process as process_module
from deployment.pe import _pe_imports
from test_updates import FakeManager, release_payload


class SecurityBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_nested_release_json_fails_cleanly_and_a_later_check_recovers(self):
        manager = FakeManager()
        service = UpdateService(manager=manager, cache_dir=self.root)
        self.addCleanup(service.cancel)
        service.check()
        reply = manager.replies[-1]
        reply.data.extend(b"[" * 1500 + b"]" * 1500)
        # Call the slot directly so a regression is a test exception, rather than
        # an unhandled Python exception crossing a Qt signal boundary.
        service._finished(reply)
        self.assertEqual(service.state, "error")
        self.assertIsNone(service._reply)
        self.assertFalse(service._timer.isActive())
        service.check()
        import json
        manager.replies[-1].finish(json.dumps(release_payload()).encode())
        self.assertEqual(service.state, "available")

    def test_synchronous_cancel_during_check_notification_starts_no_request(self):
        manager = FakeManager()
        service = UpdateService(manager=manager, cache_dir=self.root)
        service.state_changed.connect(lambda state: service.cancel() if state == "checking" else None)
        service.check()
        self.assertEqual(service.state, "idle")
        self.assertEqual(manager.requests, [])
        self.assertFalse(service._timer.isActive())

    def test_reentrant_cancel_retry_creates_one_live_request_without_stale_error(self):
        manager = FakeManager()
        service = UpdateService(manager=manager, cache_dir=self.root)
        self.addCleanup(service.cancel)
        restarted = []
        def restart(state):
            if state == "checking" and not restarted:
                restarted.append(True)
                service.cancel()
                service.check()
        service.state_changed.connect(restart)
        service.check()
        self.assertEqual(len(manager.requests), 1)
        service.state_changed.disconnect(restart)
        errors = []
        service.error_occurred.connect(errors.append)
        service.state_changed.connect(lambda state: service.check() if state == "error" else None)
        reply = manager.replies[-1]
        reply.data.extend(b"invalid JSON")
        service._finished(reply)
        self.assertEqual(service.state, "checking")
        self.assertEqual(errors, [])
        self.assertEqual(len(manager.requests), 2)

    def test_corrupt_settings_recover_without_losing_unrelated_preferences(self):
        settings = QSettings(str(self.root / "prefs.ini"), QSettings.Format.IniFormat)
        settings.setValue("language", "en")
        settings.setValue("theme", "dark")
        settings.setValue("build/jobs", "9" * 10000)
        settings.setValue("project/session", "[" * 1500 + "]" * 1500)
        settings.setValue("toolchains/cache", "[" * 1500 + "]" * 1500)
        preferences = AppPreferences(ROOT / "resource", settings, QLocale("en"))
        self.assertEqual(preferences.jobs, AUTO_JOBS)
        self.assertEqual(preferences.theme, "dark")
        self.assertEqual(preferences.project_session(), {})
        self.assertEqual(preferences.toolchain_cache(), {})
        preferences.save_project_session({"project": "valid.pro"})
        self.assertEqual(preferences.project_session(), {"project": "valid.pro"})

    def test_seeded_release_field_mutations_never_escape_validation(self):
        rng = random.Random(0x515450)
        values = [None, False, True, 0, -1, 2 ** 63, "", [], {}, "\x00", "v1.0.1"]
        for _ in range(600):
            payload = copy.deepcopy(release_payload())
            if rng.randrange(2):
                payload[rng.choice(("tag_name", "draft", "prerelease", "assets", "body"))] = rng.choice(values)
            else:
                payload["assets"][0][rng.choice(("name", "size", "digest", "state", "browser_download_url"))] = rng.choice(values)
            try:
                release = parse_release(payload, "1.0.0")
            except ValueError:
                continue
            self.assertEqual(release.url, release_payload()["assets"][0]["browser_download_url"])
            self.assertGreater(release.size, 0)

    def test_redirect_host_confusion_and_generated_paths_do_not_escape(self):
        original = parse_release(release_payload()).url
        for suffix in (".evil.test", "@evil.test", ":444", "\\@evil.test", "%00.evil.test"):
            self.assertFalse(allowed_download_url("https://release-assets.githubusercontent.com" + suffix + "/x", original))
        for name in ("../escape", "a/../../escape", "C:/escape", "//host/share", "\\escape", "NUL", "CON.txt", "a:stream", "trailing. ", "a/../escape", "/../escape"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                deployment_path(self.root, name)
        rng = random.Random(0x50415448)
        for _ in range(400):
            name = "/".join("".join(rng.choices("abcXYZ019._-", k=rng.randrange(1, 16))) for _ in range(rng.randrange(1, 5)))
            try:
                target = deployment_path(self.root, name)
            except ValueError:
                continue
            self.assertTrue(target.resolve().is_relative_to(self.root))

    def test_seeded_truncated_and_mutated_pe_images_have_bounded_failures(self):
        seed = bytearray(0x600)
        seed[:2] = b"MZ"
        struct.pack_into("<I", seed, 0x3C, 0x80)
        seed[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<HHIIIHH", seed, 0x84, 0x8664, 1, 0, 0, 0, 240, 0)
        struct.pack_into("<H", seed, 0x98, 0x20B)
        struct.pack_into("<I", seed, 0x98 + 108, 16)
        struct.pack_into("<II", seed, 0x98 + 120, 0x1000, 20)
        struct.pack_into("<III", seed, 0x98 + 240 + 12, 0x1000, 0x200, 0x200)
        struct.pack_into("<5I", seed, 0x200, 0, 0, 0, 0x1040, 0x1080)
        seed[0x240:0x24D] = b"KERNEL32.dll\0"
        self.assertEqual(_pe_imports(seed), (0x8664, ["KERNEL32.dll"]))
        rng = random.Random(0x50454655)
        samples = [seed[:length] for length in range(0, len(seed), 7)]
        for _ in range(1200):
            sample = bytearray(seed)
            for _ in range(rng.randrange(1, 12)):
                sample[rng.randrange(len(sample))] = rng.randrange(256)
            samples.append(sample)
        for image in samples:
            try:
                machine, names = _pe_imports(image)
            except (ValueError, struct.error):
                continue
            self.assertIsInstance(machine, int)
            self.assertTrue(all(isinstance(name, str) for name in names))

    def test_continuous_pipe_returns_and_long_lines_remain_complete(self):
        process = BuildProcess(self.app)
        emitted = []
        process.output.connect(lambda text, stderr: emitted.append(text))
        process._streams = [[object(), getincrementaldecoder("utf-8")("replace"), False, ""]]
        reads = []
        def read(pipe, size):
            reads.append(size)
            if len(reads) > 20:
                raise AssertionError("Continuous output starved the Qt event loop")
            return 0, b"x" * size
        with (
            patch.object(process_module.win32pipe, "PeekNamedPipe", return_value=(b"", 65536, 0)),
            patch.object(process_module.win32file, "ReadFile", side_effect=read),
        ):
            process._forward_output()
        self.assertLessEqual(len(process._streams[0][3]), 65536)
        with patch.object(process_module.win32pipe, "PeekNamedPipe", return_value=(b"", 0, 0)):
            process._forward_output(final=True)
        self.assertEqual("".join(emitted), "x" * sum(reads))

    def test_real_job_cancels_descendants_and_round_trips_large_output(self):
        # Every launched process belongs to BuildProcess's kill-on-close job.
        # Scripts terminate themselves after 10 s even if the runner regresses.
        child_script = self.root / "child.py"
        child_script.write_text("import os,time\nfrom pathlib import Path\nPath('child.pid').write_text(str(os.getpid()))\ntime.sleep(10)\n", encoding="utf-8")
        parent_script = self.root / "parent.py"
        parent_script.write_text(
            "import os,subprocess,sys,time\n"
            "subprocess.Popen([sys.executable,'child.py'],creationflags=subprocess.CREATE_NO_WINDOW)\n"
            "os.write(1,b'A'*70000+b'\\n'+b'B'*70000)\n"
            "open('output.complete','w').close()\n"
            "time.sleep(10)\n", encoding="utf-8",
        )
        process = BuildProcess(self.app)
        outputs, result = [], []
        process.output.connect(lambda text, stderr: outputs.append(text))
        process.finished.connect(result.append)
        process.failed.connect(lambda message: result.append(message))
        process.start(Path(sys.executable), [str(parent_script)], str(self.root), dict(os.environ))
        started = time.monotonic()
        try:
            while process.running and time.monotonic() - started < 5:
                self.app.processEvents()
                if (self.root / "child.pid").is_file() and (self.root / "output.complete").is_file():
                    process.terminate()
                time.sleep(0.005)
            self.assertFalse(process.running, "Job tree cancellation exceeded five seconds")
            self.assertEqual("".join(outputs), "A" * 70000 + "\n" + "B" * 70000)
            self.assertEqual(result, [1])
            pid = int((self.root / "child.pid").read_text())
            try:
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
            except pywintypes.error as exc:
                self.assertEqual(exc.winerror, 87)
                pass  # Windows has already removed the exited process object.
            else:
                try:
                    self.assertNotEqual(win32process.GetExitCodeProcess(handle), 259)
                finally:
                    handle.Close()
        finally:
            if process.running:
                process._complete(error="Test deadline: closed the owned job")


if __name__ == "__main__":
    unittest.main()
