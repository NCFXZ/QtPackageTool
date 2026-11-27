# SPDX-License-Identifier: GPL-3.0-or-later

"""Asynchronous GitHub checks and installer downloads; never runs an installer."""

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app.metadata import APP_VERSION, RELEASE_API_URL
from .eligibility import delta_runtime_available
from .models import Asset, HELPER_NAME, MANIFEST_NAME, Release, allowed_download_url, https_host, parse_release, version_tuple


class UpdateService(QObject):
    state_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)
    release_changed = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(
        self, parent=None, *, manager=None, cache_dir=None,
        api_url=RELEASE_API_URL, current_version=APP_VERSION, installed_dir=None,
    ):
        super().__init__(parent)
        if https_host(api_url) != "api.github.com" or api_url != RELEASE_API_URL:
            raise ValueError("The update API must use the official GitHub repository.")
        self._current_version = version_tuple("v" + current_version)
        self._current_version_text = current_version
        self._installed_dir = Path(installed_dir).absolute() if installed_dir is not None else None
        self._api_url = api_url
        self._manager = manager if manager is not None else QNetworkAccessManager(self)
        if cache_dir is None:
            local_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
            cache_dir = Path(local_data) / "QtPackageTool" / "updates"
        self._cache_dir = Path(cache_dir).absolute()
        self._reply = None
        self._file = None
        self._part = None
        self._installer = None
        self._work_dir = None
        self._hasher = None
        self._received = 0
        self._metadata = bytearray()
        self._redirects = 0
        self._operation = 0
        self._selected_asset: Asset | None = None
        self._full_fallback_used = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._download_failed("The update request timed out. Try again."))
        self.state = "idle"
        self.release: Release | None = None
        self.error_message = ""

    @property
    def selected_asset(self) -> Asset | None:
        return self._selected_asset

    @property
    def download_kind(self) -> str:
        return "delta" if self.release is not None and self._selected_asset is not None and self._selected_asset == self.release.delta else "full"

    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def check(self) -> None:
        if self.state in ("checking", "downloading"):
            return
        self._discard_download()
        self._operation += 1
        operation = self._operation
        self._selected_asset = None
        self._full_fallback_used = False
        self.release = None
        self.error_message = ""
        self._metadata.clear()
        self._redirects = 0
        self._set_state("checking")
        if self.state != "checking" or self._operation != operation:
            return
        self._timer.start(60000)
        self._request(self._api_url)

    def download(self) -> None:
        if self.release is None or self.state in ("checking", "downloading", "ready", "current"):
            return
        self._full_fallback_used = False
        asset = self.release.delta if self.release.delta is not None and self._can_use_delta() else self.release.full_asset
        self._start_download(asset)

    def _can_use_delta(self) -> bool:
        """Read only the small installed manifest; the external helper verifies the tree."""
        if self._installed_dir is None or not delta_runtime_available():
            return False
        try:
            if not (self._installed_dir / HELPER_NAME).is_file():
                return False
            with (self._installed_dir / MANIFEST_NAME).open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                return False
            manifest = json.loads(data)
        except (OSError, ValueError, RecursionError):
            return False
        if not (
            isinstance(manifest, dict) and type(manifest.get("schema")) is int
            and manifest["schema"] == 1 and manifest.get("version") == self._current_version_text
        ):
            return False
        try:
            with (self._cache_dir / "full-only.json").open("rb") as stream:
                data = stream.read(4097)
            if len(data) > 4096:
                return True
            marker = json.loads(data)
        except (OSError, ValueError, RecursionError):
            return True
        return not (
            isinstance(marker, dict) and type(marker.get("schema")) is int
            and marker["schema"] == 1 and marker.get("base_version") == self._current_version_text
            and marker.get("target_version") == self.release.version
            and marker.get("full_sha256") == self.release.sha256
        )

    def _start_download(self, asset: Asset) -> None:
        self._operation += 1
        operation = self._operation
        self._selected_asset = asset
        self._discard_download()
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._work_dir = Path(tempfile.mkdtemp(prefix="update-", dir=self._cache_dir))
            self._part = self._work_dir / (asset.name + ".part")
            self._file = self._part.open("xb")
        except OSError:
            self._fail("Could not create the update download file.")
            return
        self._hasher = hashlib.sha256()
        self._received = 0
        self._redirects = 0
        self.error_message = ""
        self._set_state("downloading")
        if self.state != "downloading" or self._selected_asset is not asset or self._operation != operation:
            return
        self.progress_changed.emit(0, asset.size)
        if self.state != "downloading" or self._selected_asset is not asset or self._operation != operation:
            return
        self._timer.start(15 * 60 * 1000)
        self._request(asset.url)

    def fallback_to_full(self) -> bool:
        """Switch a failed delta attempt or pre-install handoff to the full asset once."""
        if self.state not in ("downloading", "ready") or self.download_kind != "delta" or self._full_fallback_used:
            return False
        self._full_fallback_used = True
        self._close_reply()
        self._timer.stop()
        self._start_download(self.release.full_asset)
        return True

    def _download_failed(self, message: str) -> None:
        if not self.fallback_to_full():
            self._fail(message)

    def _request(self, url: str) -> None:
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(30000)
        request.setRawHeader(b"User-Agent", ("QtPackageTool/" + APP_VERSION).encode("ascii"))
        request.setRawHeader(b"Accept", b"application/vnd.github+json" if self.state == "checking" else b"application/octet-stream")
        try:
            reply = self._manager.get(request)
            self._reply = reply
            reply.setReadBufferSize(64 * 1024)
            reply.readyRead.connect(lambda: self._receive(reply))
            reply.finished.connect(lambda: self._finished(reply))
        except (RuntimeError, OSError):
            self._download_failed("Could not start the update request.")

    def _receive(self, reply) -> None:
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status != 200:
            return
        chunk = bytes(reply.readAll())
        if self.state == "checking":
            if len(self._metadata) + len(chunk) > 1024 * 1024:
                self._fail("GitHub returned invalid release information.")
                return
            self._metadata.extend(chunk)
        elif self.state == "downloading":
            asset = self._selected_asset
            if self._received + len(chunk) > asset.size:
                self._download_failed("The downloaded installer size does not match the release.")
                return
            try:
                if self._file.write(chunk) != len(chunk):
                    raise OSError("Incomplete update download write")
            except OSError:
                self._fail("Could not save the downloaded installer.")
                return
            self._hasher.update(chunk)
            self._received += len(chunk)
            self.progress_changed.emit(self._received, asset.size)

    def _finished(self, reply) -> None:
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        redirect = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
        if status in (301, 302, 303, 307, 308) and redirect is not None:
            target = reply.url().resolved(QUrl(redirect)).toString()
            allowed = target == self._api_url if self.state == "checking" else allowed_download_url(target, self._selected_asset.url)
            if not allowed or self._redirects >= 5:
                self._download_failed("GitHub returned an unexpected update redirect.")
                return
            self._redirects += 1
            self._close_reply()
            self._request(target)
            return
        if status == 404 and self.state == "checking":
            self._close_reply()
            self._timer.stop()
            self._set_state("current")
            return
        if reply.error() != QNetworkReply.NetworkError.NoError or status != 200:
            message = "GitHub is unavailable or rate limited. Try again later." if status in (403, 429) else "Could not complete the update request. Check your connection and try again."
            self._download_failed(message)
            return
        url = reply.url().toString()
        allowed = url == self._api_url if self.state == "checking" else allowed_download_url(url, self._selected_asset.url)
        if not allowed:
            self._download_failed("GitHub returned an unexpected update redirect.")
            return
        self._receive(reply)
        if reply is not self._reply:
            return
        self._close_reply()
        self._timer.stop()
        if self.state == "checking":
            try:
                payload = json.loads(self._metadata)
            except (ValueError, RecursionError):
                self._fail("GitHub returned invalid release information.")
                return
            try:
                self.release = parse_release(payload, self._current_version_text)
            except ValueError as exc:
                self._fail(str(exc))
                return
            operation = self._operation
            self.release_changed.emit(self.release)
            if self.state != "checking" or self._operation != operation:
                return
            self._set_state("available" if version_tuple("v" + self.release.version) > self._current_version else "current")
        elif self.state == "downloading":
            self._finish_download()

    def _finish_download(self) -> None:
        asset = self._selected_asset
        try:
            self._file.close()
            self._file = None
            if self._received != asset.size:
                self._download_failed("The downloaded installer size does not match the release.")
                return
            if self._hasher.hexdigest() != asset.sha256:
                self._download_failed("The downloaded installer checksum does not match the release.")
                return
            installer = self._work_dir / asset.name
            os.replace(self._part, installer)
            self._part = None
            self._installer = installer
        except OSError:
            self._fail("Could not save the downloaded installer.")
            return
        self._set_state("ready")

    def verified_installer(self) -> Path | None:
        if self.state != "ready" or self._installer is None or self._selected_asset is None:
            return None
        asset = self._selected_asset
        try:
            info = self._installer.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000 or info.st_size != asset.size:
                raise ValueError
            digest = hashlib.sha256()
            with self._installer.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != asset.sha256:
                raise ValueError
        except (OSError, ValueError):
            self._download_failed("The downloaded installer could not be verified. Download it again.")
            return None
        return self._installer

    def _close_reply(self) -> None:
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()

    def _discard_download(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        for path in (self._part, self._installer):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._part = self._installer = None
        if self._work_dir is not None:
            try:
                self._work_dir.rmdir()
            except OSError:
                pass
            self._work_dir = None

    def _fail(self, message: str) -> None:
        self._close_reply()
        self._timer.stop()
        self._discard_download()
        self.error_message = message
        operation = self._operation
        self._set_state("error")
        if self.state == "error" and self._operation == operation:
            self.error_occurred.emit(message)

    def cancel(self) -> None:
        if self.state not in ("checking", "downloading"):
            return
        self._operation += 1
        self._close_reply()
        self._timer.stop()
        self._discard_download()
        self._set_state("idle")
