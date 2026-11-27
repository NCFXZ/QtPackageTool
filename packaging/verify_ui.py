# SPDX-License-Identifier: GPL-3.0-or-later

"""Check source UI translations, layout and update states at five device scales.

Uses isolated INI preferences and the offscreen Qt platform; installed native UI
and Windows taskbar checks are separate acceptance steps.
"""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import string
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SCALES = ("1", "1.25", "1.5", "1.75", "2")


def check_catalog() -> dict:
    catalog = {}
    formatter = string.Formatter()
    for message in ET.parse(ROOT / "resource/i18n/zh_CN.ts").iter("message"):
        source = message.findtext("source", "")
        translation = message.find("translation")
        assert source and source not in catalog, f"Empty or duplicate translation source: {source!r}"
        assert translation is not None and translation.get("type") not in ("unfinished", "vanished", "obsolete"), source
        target = "".join(translation.itertext()).strip()
        assert target, source
        fields = lambda text: sorted(field for _, field, _, _ in formatter.parse(text) if field is not None)
        assert fields(source) == fields(target), f"Translation placeholders differ: {source}"
        assert re.findall(r"%\d+", source) == re.findall(r"%\d+", target), source
        catalog[source] = target
    literal_sources = set()
    for path in (ROOT / "src").rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")
            if name not in ("tr", "localized", "bind_text") or not call.args:
                continue
            source = call.args[0] if name == "tr" else call.args[-1]
            if isinstance(source, ast.Constant) and isinstance(source.value, str):
                literal_sources.add(source.value)
    missing = literal_sources - catalog.keys()
    assert not missing, f"Uncatalogued translation sources: {sorted(missing)}"
    return {"messages": len(catalog), "literal_sources": len(literal_sources), "missing": []}


def worker(scale: str, output: Path) -> dict:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = scale
    sys.path.insert(0, str(ROOT / "src"))
    from PyQt6.QtCore import QLocale, QPoint, QSettings
    from PyQt6.QtGui import QColor, QFontDatabase, QImage, QPainter
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QAbstractButton, QAbstractScrollArea, QLabel, QLineEdit, QTableWidget, QWidget
    from qfluentwidgets import SimpleCardWidget
    from app.preferences import AppPreferences
    from app.runtime import RESOURCE_DIR
    from UI.ui_main import MainUI
    from UI.ui_layout_settings import ElidedLabel

    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    for font in ("segoeui.ttf", "msyh.ttc"):
        assert QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"]) / "Fonts" / font)) >= 0
    report = {"scale": scale, "actual_dpr": app.primaryScreen().devicePixelRatio(), "layouts": [], "states": [], "screenshots": []}
    assert abs(report["actual_dpr"] - float(scale)) < .01
    cjk = re.compile(r"[\u3400-\u9fff]")

    def texts(page):
        values = []
        for widget in page.findChildren((QLabel, QAbstractButton, QLineEdit)):
            values.append(widget.text())
        for widget in page.findChildren(QLineEdit):
            values.append(widget.placeholderText())
        for widget in page.findChildren(QTableWidget):
            for index in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(index)
                if item:
                    values.append(item.text())
        for widget in page.findChildren(QAbstractScrollArea):
            if hasattr(widget, "placeholderText"):
                values.append(widget.placeholderText())
        for widget in [page, *page.findChildren(QWidget)]:
            values.extend((widget.toolTip(), widget.accessibleName(), widget.accessibleDescription()))
            if callable(getattr(widget, "count", None)) and callable(getattr(widget, "itemText", None)):
                values.extend(widget.itemText(i) for i in range(widget.count()))
        return [value for value in values if value]

    with tempfile.TemporaryDirectory(dir=output) as folder:
        prefs = AppPreferences(RESOURCE_DIR, QSettings(str(Path(folder) / "ui.ini"), QSettings.Format.IniFormat), QLocale("en"))
        window = MainUI(prefs)
        window.stackedWidget.setAnimationEnabled(False)
        window.show()
        pages = (window.qt_package_settings, window.qt_package_project, window.preferences_ui, window.about_ui)
        for language in ("zh_CN", "en"):
            prefs.select_language(language)
            for theme in ("light", "dark"):
                window.preferences_ui.theme_combo.setCurrentIndex(window.preferences_ui.theme_combo.findData(theme))
                for width, height in ((1180, 680), (1180, 800), (1645, 940)):
                    window.resize(width, height)
                    for page in pages:
                        window.switchTo(page)
                        window.navigationInterface.setCurrentItem(page.objectName())
                        QTest.qWait(35)
                        failures = []
                        for scroll in page.findChildren(QAbstractScrollArea):
                            if scroll.isVisible() and scroll.horizontalScrollBar().maximum() != 0:
                                failures.append(f"horizontal scroll: {type(scroll).__name__}")
                        for label in page.findChildren(QLabel):
                            if not label.isVisible() or not label.text() or label.wordWrap() or isinstance(label, ElidedLabel):
                                continue
                            if label.width() + 1 < label.minimumSizeHint().width():
                                failures.append(f"clipped label: {label.text()!r} width={label.width()} needed={label.minimumSizeHint().width()}")
                        for button in page.findChildren(QAbstractButton):
                            if button.isVisible() and button.text():
                                if button.fontMetrics().horizontalAdvance(button.text()) > button.width():
                                    failures.append(f"clipped action: {button.text()!r}")
                        groups = {}
                        for card in page.findChildren(SimpleCardWidget):
                            groups.setdefault(card.parentWidget(), []).append((card.x(), card.width()))
                        for rects in groups.values():
                            if len(rects) > 1 and len(set(rects)) > 1:
                                failures.append(f"card edges differ: {rects}")
                        values = texts(page)
                        if language == "en":
                            failures.extend(f"untranslated English UI: {value}" for value in values if cjk.search(value) and value != "简体中文")
                        assert not failures, (scale, language, theme, width, height, page.objectName(), failures)
                        case = {"language": language, "theme": theme, "size": [width, height], "page": page.objectName(), "texts": values}
                        report["layouts"].append(case)
                        if (scale == "1.75" and height == 800) or (height == 680 and page is pages[-1]):
                            file = output / f"{language}-{theme}-{width}x{height}-{page.objectName()}.png"
                            pixmap = window.grab()
                            # Transparent Fluent surfaces normally composite over DWM;
                            # offscreen captures need the corresponding opaque backdrop.
                            capture = QImage(pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
                            capture.setDevicePixelRatio(pixmap.devicePixelRatio())
                            capture.fill(QColor("#202020" if theme == "dark" else "#f5f5f5"))
                            painter = QPainter(capture)
                            painter.drawPixmap(0, 0, pixmap)
                            painter.end()
                            assert capture.save(str(file))
                            report["screenshots"].append(str(file))

                about = window.about_ui
                window.resize(1180, 800)
                window.switchTo(about)
                about.set_update_install_allowed(True)
                anchors = []
                for state in ("idle", "checking", "current", "available", "downloading", "ready", "error"):
                    if state == "error":
                        about.set_update_state("checking")
                    about.set_update_state(state, "1.0.1", "" if state not in ("available", "downloading", "ready") else "Release notes\nSecond line", "The update request timed out. Try again." if state == "error" else "")
                    if state == "downloading":
                        about.set_update_progress(50, 100)
                    QTest.qWait(35)
                    action = about.update_check_button
                    assert action.width() == about.github_button.width()
                    assert action.fontMetrics().horizontalAdvance(action.text()) + (24 if action.text() else 0) <= action.width()
                    assert state not in ("idle", "available", "ready", "error") or not action.icon().isNull()
                    anchors.append((action.mapTo(about, QPoint(0, 0)).y(), action.mapTo(about, QPoint(action.width(), 0)).x()))
                    report["states"].append({"language": language, "theme": theme, "state": state, "text": action.text(), "width": action.width()})
                assert len(set(anchors)) == 1, anchors
                about.set_update_state("idle")
                window.switchTo(window.qt_package_project)
                QTest.qWait(35)
                assert window.qt_package_project.package_toggle_button.width() == window.qt_package_project.package_folder_button.width()
        # A language round trip refreshes every page without leaving Chinese in English.
        prefs.select_language("zh_CN")
        QTest.qWait(30)
        first = [texts(page) for page in pages]
        prefs.select_language("en")
        QTest.qWait(30)
        prefs.select_language("zh_CN")
        QTest.qWait(30)
        assert first == [texts(page) for page in pages]
        # Icon requests use physical pixels at each device scale.
        about_logo = [label for label in window.about_ui.findChildren(QLabel) if label.pixmap() is not None and not label.pixmap().isNull()]
        assert any(label.pixmap().width() >= round(72 * float(scale)) and abs(label.pixmap().devicePixelRatio() - float(scale)) < .01 for label in about_logo)
        report["language_roundtrip"] = True
        report["about_logo_native_dpr"] = True
        window.close()
        app.processEvents()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "logs/acceptance-1.0.0/ui")
    parser.add_argument("--scale", choices=SCALES)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.scale:
        report = worker(args.scale, args.output)
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PASS scale {args.scale}: {len(report['layouts'])} layouts, {len(report['states'])} states", flush=True)
        return
    catalog = check_catalog()
    reports = []
    for scale in SCALES:
        folder = args.output / scale
        subprocess.run([sys.executable, "-B", __file__, "--scale", scale, "--output", str(folder)], cwd=ROOT, check=True)
        reports.append(json.loads((folder / "report.json").read_text(encoding="utf-8")))
    summary = {"kind": "source/offscreen", "catalog": catalog, "scales": SCALES,
               "layouts": sum(len(report["layouts"]) for report in reports),
               "update_states": sum(len(report["states"]) for report in reports),
               "reports": [str(args.output / scale / "report.json") for scale in SCALES]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
