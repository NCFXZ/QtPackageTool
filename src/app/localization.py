# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator


def tr(source: str) -> str:
    return QCoreApplication.translate("QtPackageTool", source)


def preferred_language(locale: QLocale) -> str:
    for language in locale.uiLanguages():
        code = language.split("-")[0]
        if code == "zh":
            return "zh_CN"
        if code == "en":
            return "en"
    return "en"


def load_translators(language: str, resources: Path) -> list[QTranslator]:
    if language != "zh_CN":
        return []

    from qfluentwidgets import FluentTranslator

    locale = QLocale(language)
    qt = QTranslator()
    qt.load(locale, "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    app = QTranslator()
    if not app.load(str(resources / "i18n" / "zh_CN.qm")):
        raise RuntimeError("Could not load the Simplified Chinese translation.")
    return [translator for translator in (qt, FluentTranslator(locale), app) if not translator.isEmpty()]
