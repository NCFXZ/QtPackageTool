# SPDX-License-Identifier: GPL-3.0-or-later

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QByteArray, QCoreApplication, QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.metadata import INSTALLER_NAME, RELEASE_API_URL, REPOSITORY
from app.updates.models import allowed_download_url, parse_release, version_tuple
from app.updates.service import UpdateService


INSTALLER = b"MZ" + b"test installer data" * 100


def release_payload(version="1.0.1", installer=INSTALLER):
    tag = "v" + version
    return {
        "tag_name": tag, "draft": False, "prerelease": False, "body": "Update notes",
        "assets": [{
            "name": INSTALLER_NAME, "state": "uploaded", "size": len(installer),
            "digest": "sha256:" + hashlib.sha256(installer).hexdigest(),
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{INSTALLER_NAME}",
        }],
    }


class FakeReply(QObject):
    readyRead = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, url):
        super().__init__()
        self._url = url
        self.status = 200
        self.target = None
        self.data = bytearray()
        self.network_error = QNetworkReply.NetworkError.NoError
        self.completed = False
        self.aborted = False

    def url(self):
        return self._url

    def attribute(self, name):
        if name == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self.status
        if name == QNetworkRequest.Attribute.RedirectionTargetAttribute:
            return self.target

    def error(self):
        return self.network_error

    def setReadBufferSize(self, size):
        self.buffer_size = size

    def readAll(self):
        data = QByteArray(bytes(self.data))
        self.data.clear()
        return data

    def feed(self, data):
        self.data.extend(data)
        self.readyRead.emit()

    def finish(self, data=b""):
        if data:
            self.feed(data)
        self.completed = True
        self.finished.emit()

    def abort(self):
        self.aborted = True
        if not self.completed:
            self.network_error = QNetworkReply.NetworkError.OperationCanceledError
            self.completed = True
            self.finished.emit()


class FakeManager:
    def __init__(self):
        self.requests = []
        self.replies = []

    def get(self, request):
        self.requests.append(QNetworkRequest(request))
        reply = FakeReply(request.url())
        self.replies.append(reply)
        return reply


class UpdateModelTests(unittest.TestCase):
    def test_strict_stable_version_comparison_is_numeric(self):
        self.assertGreater(version_tuple("v1.10.0"), version_tuple("v1.9.9"))
        for tag in ("1.0.0", "v01.0.0", "v1.0", "v1.0.0-beta", "v1.0.0+build", "v1.0.0\n"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                version_tuple(tag)

    def test_release_requires_exact_installer_and_api_checksum(self):
        payload = release_payload()
        release = parse_release(payload)
        self.assertEqual((release.version, release.notes, release.size), ("1.0.1", "Update notes", len(INSTALLER)))
        mutations = (
            lambda p: p.update(prerelease=True),
            lambda p: p.update(draft=True),
            lambda p: p["assets"][0].update(name="QtPackageTool.zip"),
            lambda p: p["assets"].append(copy.deepcopy(p["assets"][0])),
            lambda p: p["assets"][0].update(state="new"),
            lambda p: p["assets"][0].update(size=True),
            lambda p: p["assets"][0].update(size=2 ** 31),
            lambda p: p["assets"][0].pop("digest"),
            lambda p: p["assets"][0].update(digest="sha1:" + "a" * 40),
            lambda p: p["assets"][0].update(browser_download_url="https://example.com/installer.exe"),
            lambda p: p["assets"][0].update(browser_download_url=release.url.replace("/v1.0.1/", "/v1.0.2/")),
        )
        for mutate in mutations:
            altered = copy.deepcopy(payload)
            mutate(altered)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                parse_release(altered)

    def test_redirects_allow_only_https_github_release_hosts(self):
        original = parse_release(release_payload()).url
        self.assertTrue(allowed_download_url(original, original))
        self.assertTrue(allowed_download_url("https://release-assets.githubusercontent.com/asset?token=test", original))
        for url in (
            "http://release-assets.githubusercontent.com/asset", "https://evil.example/asset",
            "https://release-assets.githubusercontent.com.evil.example/asset",
            "https://user@release-assets.githubusercontent.com/asset",
            "https://release-assets.githubusercontent.com:444/asset",
            original.replace(REPOSITORY, "someone/else"), original + "?other=asset",
        ):
            with self.subTest(url=url):
                self.assertFalse(allowed_download_url(url, original))


class UpdateServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name)
        self.manager = FakeManager()
        self.service = UpdateService(manager=self.manager, cache_dir=self.cache, current_version="1.0.0")
        self.addCleanup(self.service.cancel)
        self.errors = []
        self.states = []
        self.service.error_occurred.connect(self.errors.append)
        self.service.state_changed.connect(self.states.append)

    def check_release(self, payload=None):
        self.service.check()
        self.manager.replies[-1].finish(json.dumps(payload or release_payload()).encode())

    def test_check_latest_download_and_reverify_installer(self):
        self.check_release()
        self.assertEqual(self.service.state, "available")
        self.assertEqual(self.manager.requests[0].url().toString(), RELEASE_API_URL)
        request = self.manager.requests[0]
        self.assertEqual(request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute), QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        self.assertEqual(request.transferTimeout(), 30000)
        progress = []
        self.service.progress_changed.connect(lambda done, total: progress.append((done, total)))
        self.service.download()
        reply = self.manager.replies[-1]
        reply.feed(INSTALLER[:70])
        reply.finish(INSTALLER[70:])
        self.assertEqual(self.service.state, "ready")
        installer = self.service.verified_installer()
        self.assertEqual(installer.read_bytes(), INSTALLER)
        self.assertEqual(installer.name, INSTALLER_NAME)
        self.assertEqual(progress[-1], (len(INSTALLER), len(INSTALLER)))
        self.assertEqual(list(self.cache.rglob("*.part")), [])
        self.assertEqual(self.errors, [])
        installer.write_bytes(b"X" * len(INSTALLER))
        self.assertIsNone(self.service.verified_installer())
        self.assertEqual(self.service.state, "error")
        self.assertFalse(installer.exists())

    def test_current_and_older_releases_never_download(self):
        for version in ("1.0.0", "0.9.9"):
            with self.subTest(version=version):
                self.check_release(release_payload(version))
                self.assertEqual(self.service.state, "current")
                previous = len(self.manager.requests)
                self.service.download()
                self.assertEqual(len(self.manager.requests), previous)

    def test_missing_release_and_rate_limit_have_distinct_states(self):
        self.service.check()
        self.manager.replies[-1].status = 404
        self.manager.replies[-1].finish()
        self.assertEqual(self.service.state, "current")
        self.assertEqual(self.errors, [])
        self.service.check()
        self.manager.replies[-1].status = 403
        self.manager.replies[-1].finish()
        self.assertEqual(self.service.state, "error")
        self.assertIn("rate limited", self.errors[-1])

    def test_invalid_metadata_and_missing_digest_fail_closed(self):
        self.service.check()
        self.manager.replies[-1].finish(b"<html>not json</html>")
        self.assertEqual(self.service.state, "error")
        payload = release_payload()
        payload["assets"][0].pop("digest")
        self.check_release(payload)
        self.assertEqual(self.service.state, "error")
        self.assertIn("checksum", self.errors[-1])
        self.assertEqual(list(self.cache.rglob("*")), [])

    def test_download_redirects_discard_redirect_body_and_verify_final_data(self):
        self.check_release()
        self.service.download()
        first = self.manager.replies[-1]
        first.status = 302
        first.target = QUrl("https://release-assets.githubusercontent.com/asset?token=test")
        first.finish(b"redirect body must not enter the installer")
        self.assertEqual(self.service.state, "downloading")
        self.manager.replies[-1].finish(INSTALLER)
        self.assertEqual(self.service.verified_installer().read_bytes(), INSTALLER)

    def test_unsafe_redirect_and_redirect_loop_are_rejected(self):
        for target in ("http://release-assets.githubusercontent.com/asset", "https://evil.example/setup.exe"):
            with self.subTest(target=target):
                self.check_release()
                self.service.download()
                reply = self.manager.replies[-1]
                reply.status, reply.target = 302, QUrl(target)
                reply.finish()
                self.assertEqual(self.service.state, "error")
                self.assertEqual(list(self.cache.rglob("*.part")), [])
        self.check_release()
        self.service.download()
        for _ in range(6):
            reply = self.manager.replies[-1]
            reply.status, reply.target = 302, QUrl("https://release-assets.githubusercontent.com/asset")
            reply.finish()
        self.assertEqual(self.service.state, "error")

    def test_short_oversized_and_wrong_checksum_downloads_leave_no_installer(self):
        for data in (INSTALLER[:-1], INSTALLER + b"extra", b"X" * len(INSTALLER)):
            with self.subTest(length=len(data)):
                self.check_release()
                self.service.download()
                self.manager.replies[-1].finish(data)
                self.assertEqual(self.service.state, "error")
                self.assertIsNone(self.service.verified_installer())
                self.assertEqual(list(self.cache.rglob("*.part")), [])
                self.assertEqual(list(self.cache.rglob("*.exe")), [])

    def test_cancel_discards_partial_data_ignores_late_reply_and_can_retry(self):
        self.check_release()
        self.service.download()
        first = self.manager.replies[-1]
        first.feed(INSTALLER[:50])
        self.service.cancel()
        self.assertEqual(self.service.state, "idle")
        self.assertTrue(first.aborted)
        self.assertEqual(list(self.cache.rglob("*.part")), [])
        first.finish(INSTALLER)
        self.assertEqual(self.service.state, "idle")
        self.service.download()
        self.manager.replies[-1].finish(INSTALLER)
        self.assertEqual(self.service.state, "ready")

    def test_timeout_and_network_failure_allow_new_requests(self):
        self.service.check()
        self.service.check()
        self.assertEqual(len(self.manager.requests), 1)
        self.service._timer.timeout.emit()
        self.assertEqual(self.service.state, "error")
        self.assertIn("timed out", self.errors[-1])
        self.service.check()
        reply = self.manager.replies[-1]
        reply.status = None
        reply.network_error = QNetworkReply.NetworkError.HostNotFoundError
        reply.finish()
        self.assertEqual(self.service.state, "error")
        self.check_release()
        self.assertEqual(self.service.state, "available")

    def test_available_signal_can_immediately_start_download(self):
        self.service.state_changed.connect(lambda state: self.service.download() if state == "available" else None)
        self.check_release()
        self.assertEqual(self.service.state, "downloading")
        self.assertEqual(len(self.manager.requests), 2)
        self.manager.replies[-1].finish(INSTALLER)
        self.assertEqual(self.service.state, "ready")

    def test_cache_write_failure_and_untrusted_api_are_rejected(self):
        block = self.cache / "file"
        block.write_text("not a directory", encoding="utf-8")
        self.service._cache_dir = block
        self.check_release()
        self.service.download()
        self.assertEqual(self.service.state, "error")
        self.assertIn("create", self.errors[-1])
        with self.assertRaises(ValueError):
            UpdateService(manager=self.manager, cache_dir=self.cache, api_url="http://api.github.com/releases/latest")
