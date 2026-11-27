# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
from datetime import datetime, timezone

from loguru import logger
from PyQt6.QtCore import QByteArray, QCoreApplication, QLocale, QObject, QSettings, pyqtSignal

from app.localization import load_translators, preferred_language


THEME_CHOICES = ("system", "light", "dark")
CPU_COUNT = os.cpu_count() or 8
AUTO_JOBS = 0
# Automatic uses every CPU core; the fixed choices are powers of two up to the core count.
JOB_CHOICES = (AUTO_JOBS, *(2 ** power for power in range(10) if 2 ** power <= CPU_COUNT))


class AppPreferences(QObject):
    language_changed = pyqtSignal()
    theme_changed = pyqtSignal(str)
    auto_download_updates_changed = pyqtSignal(bool)

    def __init__(self, resources, storage: QSettings | None = None, locale: QLocale | None = None):
        super().__init__()
        self._storage = storage if storage is not None else QSettings("QtPackageTool", "QtPackageTool")
        self._resources = resources
        self._translators = []
        saved_language = self._storage.value("language", "", type=str)
        self.language = (
            saved_language if saved_language in ("en", "zh_CN")
            else preferred_language(locale if locale is not None else QLocale.system())
        )
        saved_theme = self._storage.value("theme", "system", type=str)
        self.theme = saved_theme if saved_theme in THEME_CHOICES else "system"
        saved_jobs = str(self._storage.value("build/jobs", AUTO_JOBS))
        try:
            jobs = int(saved_jobs) if saved_jobs.isdecimal() else AUTO_JOBS
        except ValueError:
            jobs = AUTO_JOBS
        self.jobs = jobs if jobs in JOB_CHOICES else AUTO_JOBS
        self.auto_download_updates = self._storage.value("updates/autoDownload", True, type=bool)
        if not self._install_language(self.language):
            self.language = "en"

    def _install_language(self, language: str) -> bool:
        try:
            translators = load_translators(language, self._resources)
        except RuntimeError as exc:
            logger.warning(str(exc))
            return False
        app = QCoreApplication.instance()
        for translator in self._translators:
            app.removeTranslator(translator)
        for translator in translators:
            app.installTranslator(translator)
        self._translators = translators
        return True

    def select_language(self, language: str) -> None:
        if language in ("en", "zh_CN") and language != self.language:
            if self._install_language(language):
                self.language = language
                self._storage.setValue("language", language)
                self._storage.sync()
            self.language_changed.emit()

    def project_session(self) -> dict:
        try:
            session = json.loads(self._storage.value("project/session", "", type=str) or "{}")
        except (ValueError, RecursionError):
            return {}
        return session if isinstance(session, dict) else {}

    def save_project_session(self, session: dict) -> None:
        self._storage.setValue("project/session", json.dumps(session, ensure_ascii=False))
        self._storage.sync()

    @property
    def build_jobs(self) -> int:
        """The number of parallel compile commands, resolving the automatic choice."""
        return CPU_COUNT if self.jobs == AUTO_JOBS else self.jobs

    def select_jobs(self, jobs: int) -> None:
        if jobs in JOB_CHOICES and jobs != self.jobs:
            self.jobs = jobs
            self._storage.setValue("build/jobs", jobs)
            self._storage.sync()

    def toolchain_cache(self) -> dict:
        try:
            cache = json.loads(self._storage.value("toolchains/cache", "", type=str) or "{}")
        except (ValueError, RecursionError):
            return {}
        return cache if isinstance(cache, dict) else {}

    def save_toolchain_cache(self, cache: dict) -> None:
        self._storage.setValue("toolchains/cache", json.dumps(cache, ensure_ascii=False))
        self._storage.sync()

    def window_geometry(self) -> QByteArray:
        geometry = self._storage.value("window/geometry")
        return geometry if isinstance(geometry, QByteArray) else QByteArray()

    def save_window_geometry(self, geometry: QByteArray) -> None:
        self._storage.setValue("window/geometry", geometry)
        self._storage.sync()

    def select_theme(self, theme: str) -> None:
        if theme in THEME_CHOICES and theme != self.theme:
            self.theme = theme
            self._storage.setValue("theme", theme)
            self._storage.sync()
            self.theme_changed.emit(theme)

    def select_auto_download_updates(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled != self.auto_download_updates:
            self.auto_download_updates = enabled
            self._storage.setValue("updates/autoDownload", enabled)
            self._storage.sync()
            self.auto_download_updates_changed.emit(enabled)

    def update_check_due(self) -> bool:
        saved = str(self._storage.value("updates/lastCheck", ""))
        try:
            last_check = datetime.fromisoformat(saved)
            age = (datetime.now(timezone.utc) - last_check).total_seconds()
        except (TypeError, ValueError):
            return True
        return not 0 <= age < 86400

    def mark_update_checked(self) -> None:
        self._storage.setValue("updates/lastCheck", datetime.now(timezone.utc).isoformat())
        self._storage.sync()

    def mark_update_pending(self) -> None:
        self._storage.remove("updates/lastCheck")
        self._storage.sync()
