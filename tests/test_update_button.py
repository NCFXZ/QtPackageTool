# SPDX-License-Identifier: GPL-3.0-or-later

"""The version-row action reflects real update state without extra status rows."""

import os
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QLocale, QPoint, QSettings, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel
from qfluentwidgets import FluentIcon, Theme

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.preferences import AppPreferences
from UI.ui_about import AboutUI
from UI.ui_package_project import QtPackageProjectUI


class UpdateButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        storage = QSettings(str(Path(temporary.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.preferences = AppPreferences(ROOT / "resource", storage, QLocale("en"))
        self.addCleanup(self.preferences.select_language, "en")
        self.about = AboutUI()
        self.about.resize(960, 800)
        self.about.show()
        self.addCleanup(self.about.close)
        self.app.processEvents()
        self.button = self.about.update_check_button

    def _click(self):
        QTest.mouseClick(self.button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def test_visual_states_dispatch_the_corresponding_action(self):
        actions = []
        self.about.update_check_requested.connect(lambda: actions.append("check"))
        self.about.update_download_requested.connect(lambda: actions.append("download"))
        self.about.update_cancel_requested.connect(lambda: actions.append("cancel"))
        self.about.update_install_requested.connect(lambda: actions.append("install"))
        self.about.set_update_install_allowed(True)
        states = (
            ("idle", "Check for Updates", "check"),
            ("checking", "", "cancel"),
            ("current", "", "check"),
            ("available", "Download 1.0.1", "download"),
            ("downloading", "", "cancel"),
            ("ready", "Install 1.0.1", "install"),
            ("error", "Check failed", "check"),
        )
        for state, text, action in states:
            with self.subTest(state=state):
                if state == "error":
                    self.about.set_update_state("checking")
                self.about.set_update_state(state, version="1.0.1")
                self.app.processEvents()
                self.assertTrue(self.button.isVisible())
                self.assertEqual(self.button.text(), text)
                self.assertTrue(self.button.accessibleName())
                self._click()
                self.assertEqual(actions[-1], action)
        self.assertEqual(len(actions), len(states))

    def test_symbol_states_paint_icons_and_progress_arc_grows_with_real_progress(self):
        def foreground_pixels():
            self.app.processEvents()
            image = self.button.grab().toImage()
            center_x, center_y = image.width() // 2, image.height() // 2
            extent = round(12 * image.devicePixelRatio())
            return sum(
                1
                for x in range(center_x - extent, center_x + extent)
                for y in range(center_y - extent, center_y + extent)
                if (color := image.pixelColor(x, y)).red() < 80
                and color.green() < 100 and color.blue() < 115
            )

        self.button.clearFocus()
        for state in ("checking", "current"):
            with self.subTest(state=state):
                self.about.set_update_state(state)
                self.assertEqual(self.button.text(), "")
                self.assertGreater(foreground_pixels(), 12)
        self.about.set_update_state("downloading", version="1.0.1")
        self.about.set_update_progress(25, 100)
        quarter_arc = foreground_pixels()
        self.about.set_update_progress(75, 100)
        three_quarter_arc = foreground_pixels()
        self.assertGreater(quarter_arc, 12)
        self.assertGreater(three_quarter_arc, quarter_arc * 1.5)

    def test_progress_uses_transferred_bytes_and_stops_spinning_when_known(self):
        self.about.set_update_state("downloading", version="1.0.1")
        self.assertIsNone(self.button.progress)
        self.assertTrue(self.button.animation_running)
        for received, total, expected, text in (
            (25, 100, 0.25, "25%"),
            (1, 3, 1 / 3, "33%"),
            (75, 100, 0.75, "75%"),
            (200, 100, 1.0, "100%"),
            (-10, 100, 0.0, "0%"),
        ):
            with self.subTest(received=received, total=total):
                self.about.set_update_progress(received, total)
                self.assertAlmostEqual(self.button.progress, expected)
                self.assertIn(text, self.button.toolTip())
                self.assertFalse(self.button.animation_running)
                QTest.qWait(25)
                self.assertAlmostEqual(self.button.progress, expected)
        self.about.set_update_progress(0, 0)
        self.assertIsNone(self.button.progress)
        self.assertTrue(self.button.animation_running)

    def test_hiding_the_page_pauses_the_spinner_and_showing_resumes_it(self):
        self.about.set_update_state("checking")
        self.assertTrue(self.button.animation_running)
        QTest.qWait(60)
        before = self.button.rotation
        QTest.qWait(60)
        self.assertNotEqual(self.button.rotation, before)
        self.about.hide()
        self.app.processEvents()
        self.assertFalse(self.button.animation_running)
        paused = self.button.rotation
        QTest.qWait(70)
        self.assertEqual(self.button.rotation, paused)
        self.about.show()
        self.app.processEvents()
        self.assertTrue(self.button.animation_running)
        QTest.qWait(60)
        self.assertNotEqual(self.button.rotation, paused)

    def test_idle_and_current_do_not_run_animation_even_after_hidden_transition(self):
        for state in ("idle", "current"):
            with self.subTest(state=state):
                self.about.set_update_state("checking")
                self.about.hide()
                self.about.set_update_state(state)
                self.about.show()
                self.app.processEvents()
                self.assertFalse(self.button.animation_running)
                before = self.button.rotation
                QTest.qWait(35)
                self.assertEqual(self.button.rotation, before)

    def test_install_gate_survives_language_refresh_and_chinese_text_is_exact(self):
        installed = []
        self.about.update_install_requested.connect(lambda: installed.append(True))
        self.about.set_update_state("ready", version="1.0.1")
        self.about.set_update_install_allowed(True)
        self.assertEqual(self.button.text(), "Install 1.0.1")
        self._click()
        self.assertEqual(len(installed), 1)
        # The controller closes this gate for source runs and while a build is active.
        self.about.set_update_install_allowed(False)
        self.preferences.select_language("zh_CN")
        self.app.processEvents()
        self.assertEqual(self.button.text(), "安装1.0.1")
        self.assertFalse(self.button.isEnabled())
        self._click()
        self.assertEqual(len(installed), 1)
        self.about.set_update_install_allowed(True)
        self.assertTrue(self.button.isEnabled())
        self._click()
        self.assertEqual(len(installed), 2)

    def test_failure_uses_only_the_action_and_tooltip_without_a_status_row(self):
        self.about.set_update_state("idle")
        self.app.processEvents()
        visible_labels = Counter(label.text() for label in self.about.findChildren(QLabel) if label.isVisible())
        parent = self.button.parentWidget()
        row_height = parent.height()
        message = "The update request timed out. Try again."
        self.about.set_update_state("error", version="1.0.1", notes="not an error row", message=message)
        self.app.processEvents()
        self.assertEqual(self.button.text(), "Check failed")
        self.assertIn(message, self.button.toolTip())
        self.assertIn("Click to retry", self.button.toolTip())
        self.assertEqual(self.button.accessibleDescription(), self.button.toolTip())
        self.assertFalse(self.about.update_panel.isVisible())
        self.assertEqual(parent.height(), row_height)
        self.assertEqual(
            Counter(label.text() for label in self.about.findChildren(QLabel) if label.isVisible()),
            visible_labels,
        )
        retried = []
        self.about.update_check_requested.connect(lambda: retried.append(True))
        self.preferences.select_language("zh_CN")
        self.app.processEvents()
        self.assertEqual(self.button.text(), "检查失败")
        self.assertNotIn(message, self.button.toolTip())
        self.assertIn("重试", self.button.toolTip())
        self._click()
        self.assertEqual(retried, [True])

    def test_duplicate_error_callbacks_keep_the_operation_that_failed(self):
        checked = []
        self.about.update_check_requested.connect(lambda: checked.append(True))
        for source, english, chinese in (
            ("checking", "Check failed", "检查失败"),
            ("current", "Check failed", "检查失败"),
            ("downloading", "Download failed", "下载失败"),
            ("ready", "Update failed", "更新失败"),
        ):
            with self.subTest(source=source):
                self.preferences.select_language("en")
                self.about.set_update_state(source, version="1.0.1")
                self.about.set_update_state("error", version="1.0.1")
                self.assertEqual(self.button.text(), english)
                # The service changes state first, then supplies the error detail.
                self.about.set_update_state("error", version="1.0.1", message="Failure details")
                self.assertEqual(self.button.text(), english)
                self.assertIn("Failure details", self.button.toolTip())
                self.preferences.select_language("zh_CN")
                self.app.processEvents()
                self.assertEqual(self.button.text(), chinese)
                self.about.set_update_state("error", version="1.0.1", message="More details")
                self.assertEqual(self.button.text(), chinese)
                self._click()
        self.assertEqual(len(checked), 4)

    def test_text_actions_have_visible_icons_and_failures_still_retry(self):
        retried = []
        self.about.update_check_requested.connect(lambda: retried.append(True))
        expected_failure_icon = FluentIcon.CLOSE.icon(Theme.LIGHT, color=QColor("#000000")).pixmap(16, 16).toImage()
        for state in ("idle", "available", "ready"):
            with self.subTest(state=state):
                self.about.set_update_state(state, version="1.0.1")
                self.assertTrue(self.button.isVisible())
                self.assertTrue(self.button.text())
                self.assertFalse(self.button.icon().isNull())
                self.assertFalse(self.button.icon().pixmap(self.button.iconSize()).isNull())
        for source in ("checking", "downloading", "ready"):
            with self.subTest(error_source=source):
                self.about.set_update_state(source, version="1.0.1")
                self.about.set_update_state("error", version="1.0.1", message="Failure details")
                self.assertFalse(self.button.icon().isNull())
                self.assertEqual(self.button.icon().pixmap(16, 16).toImage(), expected_failure_icon)
                self.assertTrue(self.button.isEnabled())
                self._click()
        self.assertEqual(len(retried), 3)

    def test_about_action_pair_keeps_equal_width_through_languages_and_states(self):
        for language in ("zh_CN", "en", "zh_CN"):
            self.preferences.select_language(language)
            widths = []
            for state in ("idle", "checking", "current", "available", "downloading", "ready", "error"):
                with self.subTest(language=language, state=state):
                    self.about.set_update_state(state, version="1.0.1")
                    self.app.processEvents()
                    self.assertEqual(self.button.width(), self.about.github_button.width())
                    self.assertEqual(self.button.height(), self.about.github_button.height())
                    self.assertGreaterEqual(self.about.github_button.width(), self.about.github_button.sizeHint().width())
                    widths.append(self.button.width())
            self.assertEqual(len(set(widths)), 1)

    def test_packaging_action_pair_is_compact_in_chinese_and_fits_english(self):
        page = QtPackageProjectUI()
        self.addCleanup(page.close)
        page.resize(960, 800)
        page.show()
        for language in ("zh_CN", "en", "zh_CN"):
            self.preferences.select_language(language)
            widths = []
            for action in ("Start Packaging", "Stop Packaging", "Stopping..."):
                with self.subTest(language=language, action=action):
                    page.set_packaging_action(action)
                    self.app.processEvents()
                    primary, folder = page.package_toggle_button, page.package_folder_button
                    self.assertEqual(primary.width(), folder.width())
                    self.assertEqual(primary.height(), folder.height())
                    widths.append(primary.width())
                    if language == "zh_CN":
                        self.assertLess(primary.width(), 184)
                    else:
                        self.assertGreaterEqual(primary.width(), primary.sizeHint().width())
                        self.assertGreaterEqual(folder.width(), folder.sizeHint().width())
            self.assertEqual(len(set(widths)), 1)

    def test_focus_does_not_add_an_inner_border_and_space_still_checks(self):
        checked = []
        self.about.update_check_requested.connect(lambda: checked.append(True))
        self.about.set_update_state("current")
        QTest.mouseMove(self.about, QPoint(5, 5))
        self.button.clearFocus()
        self.app.processEvents()
        self.assertFalse(self.button.underMouse())
        unfocused = self.button.grab().toImage()
        for reason in (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason):
            with self.subTest(reason=reason):
                self.button.setFocus(reason)
                self.app.processEvents()
                self.assertTrue(self.button.hasFocus())
                self.assertFalse(self.button.underMouse())
                self.assertEqual(self.button.grab().toImage(), unfocused)
                self.button.clearFocus()
        self.button.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(self.button, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertEqual(checked, [True])
