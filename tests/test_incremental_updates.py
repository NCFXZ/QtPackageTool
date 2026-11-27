# SPDX-License-Identifier: GPL-3.0-or-later

"""Optional delta transport failures retry the mandatory full installer once."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtCore import QCoreApplication, QUrl
from PyQt6.QtNetwork import QNetworkReply

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.metadata import INSTALLER_NAME, REPOSITORY
from app.updates.models import HELPER_NAME, MANIFEST_NAME, parse_release
from app.updates.service import UpdateService
from test_updates import FakeManager, INSTALLER, release_payload


# The service treats the verified ZIP as opaque; the external helper validates its entries.
DELTA = b"PK\x03\x04opaque delta installer ZIP fixture"
DELTA_NAME = "QtPackageTool-Update-1.0.0-to-1.0.1-x64.zip"
TARGET_MANIFEST = json.dumps({"schema": 1, "version": "1.0.1", "files": [
    {"path": "QtPackageTool.exe", "size": len(INSTALLER), "sha256": hashlib.sha256(INSTALLER).hexdigest()},
]}).encode()


def incremental_release():
    payload = release_payload()
    payload["assets"].append({
        "name": DELTA_NAME, "state": "uploaded", "size": len(DELTA),
        "digest": "sha256:" + hashlib.sha256(DELTA).hexdigest(),
        "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v1.0.1/{DELTA_NAME}",
    })
    payload["assets"].append({
        "name": MANIFEST_NAME, "state": "uploaded", "size": len(TARGET_MANIFEST),
        "digest": "sha256:" + hashlib.sha256(TARGET_MANIFEST).hexdigest(),
        "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v1.0.1/{MANIFEST_NAME}",
    })
    return payload


class IncrementalModelTests(unittest.TestCase):
    def test_delta_requires_a_unique_valid_pinned_target_manifest_but_full_does_not(self):
        release = parse_release(incremental_release(), "1.0.0")
        self.assertIsNotNone(release.delta)
        self.assertEqual((release.file_manifest.name, release.file_manifest.size, release.file_manifest.sha256),
                         (MANIFEST_NAME, len(TARGET_MANIFEST), hashlib.sha256(TARGET_MANIFEST).hexdigest()))
        mutations = (
            lambda p: p["assets"].pop(2),
            lambda p: p["assets"].append(copy.deepcopy(p["assets"][2])),
            lambda p: p["assets"][2].update(state="new"),
            lambda p: p["assets"][2].update(size=True),
            lambda p: p["assets"][2].update(size=0),
            lambda p: p["assets"][2].update(size=1024 * 1024 + 1),
            lambda p: p["assets"][2].pop("digest"),
            lambda p: p["assets"][2].update(digest="sha256:" + "x" * 64),
            lambda p: p["assets"][2].update(browser_download_url="https://example.com/files.json"),
            lambda p: p["assets"][2].update(browser_download_url=p["assets"][2]["browser_download_url"].replace("/v1.0.1/", "/v1.0.2/")),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                payload = incremental_release()
                mutate(payload)
                release = parse_release(payload, "1.0.0")
                self.assertIsNone(release.file_manifest)
                self.assertIsNone(release.delta)
                self.assertEqual(release.full_asset.name, INSTALLER_NAME)
                self.assertEqual(release.size, len(INSTALLER))

    def test_optional_delta_keeps_full_metadata_and_matches_exact_base_and_target(self):
        payload = incremental_release()
        full = parse_release(payload)
        self.assertIsNone(full.delta)
        release = parse_release(payload, "1.0.0")
        self.assertEqual((release.version, release.url, release.size, release.sha256),
                         (full.version, full.url, full.size, full.sha256))
        self.assertEqual(release.full_asset.name, INSTALLER_NAME)
        self.assertEqual((release.delta.name, release.delta.size, release.delta.sha256),
                         (DELTA_NAME, len(DELTA), hashlib.sha256(DELTA).hexdigest()))
        for current in ("0.9.9", "1.0.1", "1.0.2", "v1.0.0", "01.0.0", None):
            with self.subTest(current=current):
                self.assertIsNone(parse_release(payload, current).delta)

    def test_optional_invalid_duplicate_or_larger_delta_leaves_full_usable(self):
        mutations = (
            lambda p: p["assets"][1].update(name=DELTA_NAME.replace("1.0.0", "0.9.9")),
            lambda p: p["assets"][1].update(name=DELTA_NAME.replace("1.0.1", "1.0.2")),
            lambda p: p["assets"][1].update(name=DELTA_NAME.replace("x64", "x86")),
            lambda p: p["assets"][1].update(state="new"),
            lambda p: p["assets"][1].update(size=True),
            lambda p: p["assets"][1].update(size=0),
            lambda p: p["assets"][1].update(size=len(INSTALLER)),
            lambda p: p["assets"][1].update(size=len(INSTALLER) + 1),
            lambda p: p["assets"][1].pop("digest"),
            lambda p: p["assets"][1].update(digest="sha256:" + "x" * 64),
            lambda p: p["assets"][1].update(browser_download_url="https://example.com/delta.zip"),
            lambda p: p["assets"][1].update(browser_download_url=p["assets"][1]["browser_download_url"] + "?other=asset"),
            lambda p: p["assets"].append(copy.deepcopy(p["assets"][1])),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                payload = incremental_release()
                mutate(payload)
                release = parse_release(payload, "1.0.0")
                self.assertEqual(release.full_asset.name, INSTALLER_NAME)
                self.assertIsNone(release.delta)

    def test_valid_delta_never_substitutes_for_missing_or_invalid_full_asset(self):
        for mutate in (
            lambda p: p["assets"].pop(0),
            lambda p: p["assets"][0].pop("digest"),
            lambda p: p["assets"].append(copy.deepcopy(p["assets"][0])),
        ):
            payload = incremental_release()
            mutate(payload)
            with self.assertRaises(ValueError):
                parse_release(payload, "1.0.0")


class IncrementalServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.installed = self.root / "installed"
        self.installed.mkdir()
        (self.installed / HELPER_NAME).write_bytes(b"MZhelper")
        self.manifest = self.installed / MANIFEST_NAME
        self.manifest.write_text(json.dumps({
            "schema": 1, "version": "1.0.0",
            "files": [{"path": "not-read-by-the-gui.exe", "size": 1, "sha256": "0" * 64}],
        }), encoding="utf-8")
        self.manager = FakeManager()
        self.cache = self.root / "cache"
        self.service = UpdateService(manager=self.manager, cache_dir=self.cache,
                                     current_version="1.0.0", installed_dir=self.installed)
        runtime = patch("app.updates.service.delta_runtime_available", return_value=True)
        runtime.start()
        self.addCleanup(runtime.stop)
        self.addCleanup(self.service.cancel)
        self.errors, self.states, self.progress = [], [], []
        self.service.error_occurred.connect(self.errors.append)
        self.service.state_changed.connect(self.states.append)
        self.service.progress_changed.connect(lambda done, total: self.progress.append((done, total)))

    def check_release(self, payload=None):
        self.service.check()
        self.manager.replies[-1].finish(json.dumps(payload or incremental_release()).encode())
        self.assertEqual(self.service.state, "available")

    def start_delta(self):
        self.check_release()
        self.service.download()
        self.assertEqual(self.service.download_kind, "delta")
        self.assertEqual(self.service.selected_asset, self.service.release.delta)
        self.assertEqual(self.manager.requests[-1].url().toString(), self.service.release.delta.url)
        return self.manager.replies[-1]

    def assert_full_fallback(self):
        self.assertEqual(self.service.state, "downloading")
        self.assertEqual(self.service.download_kind, "full")
        self.assertEqual(self.service.release.version, "1.0.1")
        self.assertEqual(self.service.selected_asset, self.service.release.full_asset)
        self.assertEqual(self.manager.requests[-1].url().toString(), self.service.release.url)
        self.assertEqual(self.progress[-1], (0, len(INSTALLER)))
        self.assertEqual(self.errors, [])
        self.assertEqual(list(self.cache.rglob("*.zip")), [])

    def test_installed_manifest_gate_selects_delta_without_hashing_managed_files(self):
        reply = self.start_delta()
        reply.feed(DELTA[:7])
        reply.finish(DELTA[7:])
        self.assertEqual(self.service.state, "ready")
        downloaded = self.service.verified_installer()
        self.assertEqual((downloaded.name, downloaded.read_bytes()), (DELTA_NAME, DELTA))
        self.assertEqual(self.progress[-1], (len(DELTA), len(DELTA)))
        self.assertFalse((self.installed / "not-read-by-the-gui.exe").exists())
        self.assertEqual(self.errors, [])

    def test_source_run_uses_full_even_when_a_matching_delta_is_published(self):
        self.service = UpdateService(manager=self.manager, cache_dir=self.cache, current_version="1.0.0")
        self.addCleanup(self.service.cancel)
        self.check_release()
        self.service.download()
        self.assertEqual(self.service.download_kind, "full")
        self.assertEqual(self.manager.requests[-1].url().toString(), self.service.release.url)
        self.manager.replies[-1].finish(INSTALLER)
        self.assertEqual(self.service.verified_installer().name, INSTALLER_NAME)

    def test_missing_dotnet_runtime_uses_full_without_attempting_delta(self):
        self.check_release()
        with patch("app.updates.service.delta_runtime_available", return_value=False):
            self.service.download()
        self.assertEqual(self.service.download_kind, "full")
        self.assertEqual(self.manager.requests[-1].url().toString(), self.service.release.url)
        self.assertEqual(self.errors, [])

    def test_full_only_marker_blocks_only_the_failed_base_target_and_full_asset(self):
        self.cache.mkdir()
        marker = self.cache / "full-only.json"
        matching = {
            "schema": 1, "base_version": "1.0.0", "target_version": "1.0.1",
            "full_sha256": hashlib.sha256(INSTALLER).hexdigest(),
        }
        cases = (
            (matching, "full"),
            ({**matching, "target_version": "1.0.2"}, "delta"),
            ({**matching, "full_sha256": "0" * 64}, "delta"),
            ({**matching, "base_version": "0.9.9"}, "delta"),
            ({**matching, "schema": True}, "delta"),
            (b"not JSON", "delta"),
            (b"[" * 1500 + b"]" * 1500, "delta"),
            (b"x" * 4097, "delta"),
        )
        for data, expected in cases:
            with self.subTest(marker=str(data)[:80]):
                marker.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
                self.check_release()
                self.service.download()
                self.assertEqual(self.service.download_kind, expected)
                self.assertEqual(self.errors, [])
                self.service.cancel()
        marker.unlink()
        self.check_release()
        self.service.download()
        self.assertEqual(self.service.download_kind, "delta")

    def test_unreadable_helper_uses_full_without_failing_the_update(self):
        self.check_release()
        with patch.object(Path, "is_file", side_effect=OSError("access denied")):
            self.service.download()
        self.assertEqual(self.service.download_kind, "full")
        self.assertEqual(self.errors, [])

    def test_local_download_directory_failure_does_not_request_another_asset(self):
        blocked = self.root / "blocked"
        blocked.write_bytes(b"not a directory")
        self.service._cache_dir = blocked
        self.check_release()
        requests = len(self.manager.requests)
        self.service.download()
        self.assertEqual(self.service.state, "error")
        self.assertEqual(self.service.download_kind, "delta")
        self.assertEqual(len(self.manager.requests), requests)
        self.assertEqual(len(self.errors), 1)

    def test_missing_helper_invalid_manifest_and_wrong_version_use_full(self):
        variants = (
            b"not json", b"[]", b"x" * (1024 * 1024 + 1),
            b'{"schema":true,"version":"1.0.0"}',
            b'{"schema":2,"version":"1.0.0"}',
            b'{"schema":1,"version":"0.9.9"}',
        )
        for data in variants:
            with self.subTest(data=data[:50]):
                self.manifest.write_bytes(data)
                self.check_release()
                self.service.download()
                self.assertEqual(self.service.download_kind, "full")
                self.service.cancel()
        self.manifest.write_text('{"schema":1,"version":"1.0.0"}', encoding="utf-8")
        (self.installed / HELPER_NAME).unlink()
        self.check_release()
        self.service.download()
        self.assertEqual(self.service.download_kind, "full")

    def test_delta_http_network_and_timeout_failures_retry_full_without_error(self):
        for failure in ("http", "network", "timeout"):
            with self.subTest(failure=failure):
                reply = self.start_delta()
                before = len(self.states)
                if failure == "http":
                    reply.status = 404
                    reply.finish()
                elif failure == "network":
                    reply.status = None
                    reply.network_error = QNetworkReply.NetworkError.HostNotFoundError
                    reply.finish()
                else:
                    self.service._timer.timeout.emit()
                self.assert_full_fallback()
                self.assertNotIn("available", self.states[before:])
                self.assertNotIn("error", self.states[before:])
                # Aborted or delayed responses from the delta cannot finish the new full request.
                reply.finish(DELTA)
                self.assertEqual(self.service.state, "downloading")
                self.manager.replies[-1].finish(INSTALLER)
                self.assertEqual(self.service.state, "ready")

    def test_delta_size_and_checksum_failures_retry_full(self):
        for data in (DELTA[:-1], DELTA + b"extra", b"X" * len(DELTA)):
            with self.subTest(length=len(data)):
                reply = self.start_delta()
                reply.finish(data)
                self.assert_full_fallback()
                self.manager.replies[-1].finish(INSTALLER)
                self.assertEqual(self.service.verified_installer().read_bytes(), INSTALLER)

    def test_full_failure_after_delta_fallback_never_restarts_delta_or_full(self):
        reply = self.start_delta()
        reply.finish(b"X" * len(DELTA))
        self.assert_full_fallback()
        requests = len(self.manager.requests)
        self.manager.replies[-1].finish(b"X" * len(INSTALLER))
        self.assertEqual(self.service.state, "error")
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.service.fallback_to_full())
        self.assertEqual(len(self.manager.requests), requests)
        self.assertEqual(list(self.cache.rglob("*.part")), [])
        self.assertEqual(list(self.cache.rglob("*.exe")), [])

    def test_cancel_delta_and_cancel_full_fallback_do_not_retry_or_accept_late_data(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                reply = self.start_delta()
                reply.feed(DELTA[:3])
                if fallback:
                    self.service._timer.timeout.emit()
                    reply = self.manager.replies[-1]
                requests = len(self.manager.requests)
                self.service.cancel()
                self.assertEqual(self.service.state, "idle")
                self.assertTrue(reply.aborted)
                reply.finish(INSTALLER if fallback else DELTA)
                self.assertEqual(self.service.state, "idle")
                self.assertEqual(len(self.manager.requests), requests)
                self.assertEqual(self.errors, [])
                self.assertEqual(list(self.cache.rglob("*.part")), [])

    def test_local_write_and_rename_failures_do_not_download_full(self):
        for failure in ("write", "short write", "rename"):
            with self.subTest(failure=failure):
                reply = self.start_delta()
                requests = len(self.manager.requests)
                if failure in ("write", "short write"):
                    stream = Mock(wraps=self.service._file)
                    if failure == "write":
                        stream.write.side_effect = OSError("disk full")
                    else:
                        stream.write.return_value = 0
                    self.service._file = stream
                    reply.feed(DELTA)
                else:
                    with patch("app.updates.service.os.replace", side_effect=OSError("access denied")):
                        reply.finish(DELTA)
                self.assertEqual(self.service.state, "error")
                self.assertEqual(len(self.manager.requests), requests)
                self.assertEqual(self.service.download_kind, "delta")
                self.assertEqual(list(self.cache.rglob("*.part")), [])

    def test_tampered_ready_zip_falls_back_and_returns_no_installer(self):
        self.start_delta().finish(DELTA)
        downloaded = self.service.verified_installer()
        downloaded.write_bytes(b"X" * len(DELTA))
        self.assertIsNone(self.service.verified_installer())
        self.assert_full_fallback()
        self.assertFalse(downloaded.exists())
        self.manager.replies[-1].finish(INSTALLER)
        self.assertEqual(self.service.verified_installer().read_bytes(), INSTALLER)

    def test_pre_handoff_failure_can_switch_ready_delta_to_full_once(self):
        self.start_delta().finish(DELTA)
        self.assertTrue(self.service.fallback_to_full())
        self.assert_full_fallback()
        requests = len(self.manager.requests)
        self.assertFalse(self.service.fallback_to_full())
        self.manager.replies[-1].finish(INSTALLER)
        self.assertEqual(self.service.state, "ready")
        self.assertFalse(self.service.fallback_to_full())
        self.assertEqual(len(self.manager.requests), requests)

    def test_delta_redirect_uses_its_own_asset_and_full_on_untrusted_redirect(self):
        reply = self.start_delta()
        reply.status = 302
        reply.target = QUrl("https://release-assets.githubusercontent.com/delta?token=test")
        reply.finish(b"redirect body must not enter ZIP")
        self.manager.replies[-1].finish(DELTA)
        self.assertEqual(self.service.verified_installer().read_bytes(), DELTA)
        reply = self.start_delta()
        reply.status = 302
        reply.target = QUrl("https://example.com/delta.zip")
        reply.finish()
        self.assert_full_fallback()

    def test_cancel_from_state_callback_prevents_starting_any_asset_request(self):
        self.check_release()
        self.service.state_changed.connect(lambda state: self.service.cancel() if state == "downloading" else None)
        requests = len(self.manager.requests)
        self.service.download()
        self.assertEqual(self.service.state, "idle")
        self.assertEqual(len(self.manager.requests), requests)
        self.assertEqual(list(self.cache.rglob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
