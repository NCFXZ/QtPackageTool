# SPDX-License-Identifier: GPL-3.0-or-later

"""Page layout rules: weighted centred tables, guarded remove actions, accented actions and theme colours."""

import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QLocale, QSettings, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import PrimaryPushButton, ThemeColor, setThemeColor, themeColor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.preferences import AppPreferences
import UI.ui_layout_settings as ls
from UI.ui_about import AboutUI
from UI.ui_main import MainUI
from UI.ui_package_project import QtPackageProjectUI
from UI.ui_package_settings import QtPackageSettingsUI


class PageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _show(self, page):
        page.resize(960, 1400)
        page.show()
        self.addCleanup(page.close)
        self.app.processEvents()
        return page

    def test_tables_share_width_by_weight_and_keep_text_centred(self):
        page = self._show(QtPackageSettingsUI())
        external, dependencies = page.external_table, page.dependencies_table
        page.add_external_row("C:/work/App/config", "/config")
        self.app.processEvents()
        for table in (external, dependencies):
            widths = [table.columnWidth(column) for column in range(table.columnCount())]
            self.assertEqual(sum(widths), table.viewport().width())
        self.assertGreater(external.columnWidth(0), external.columnWidth(1) * 1.5)
        type_width = dependencies.columnWidth(1)
        self.assertLess(type_width, dependencies.columnWidth(0))
        self.assertEqual(max(range(4), key=dependencies.columnWidth), 2)
        self.assertTrue(external.item(0, 0).textAlignment() & Qt.AlignmentFlag.AlignHCenter)

        page.resize(1200, 1400)
        self.app.processEvents()
        self.assertEqual(sum(external.columnWidth(c) for c in range(2)), external.viewport().width())

    def test_remove_is_enabled_only_while_rows_are_selected(self):
        page = self._show(QtPackageSettingsUI())
        table, remove = page.external_table, page.external_remove_button
        self.assertFalse(remove.isEnabled())
        self.assertFalse(page.dependencies_remove_button.isEnabled())
        page.add_external_row("C:/work/a.txt", "/a.txt")
        self.assertFalse(remove.isEnabled())
        table.selectRow(0)
        self.assertTrue(remove.isEnabled())
        remove.click()
        self.assertEqual(table.rowCount(), 0)
        self.assertFalse(remove.isEnabled())

    def test_add_buttons_sit_in_the_card_header(self):
        page = self._show(QtPackageSettingsUI())
        table = page.external_table
        for button in (page.external_file_button, page.external_folder_button):
            self.assertLess(button.mapTo(page, button.rect().center()).y(), table.mapTo(page, table.rect().topLeft()).y())
        remove = page.external_remove_button
        self.assertGreater(remove.mapTo(page, remove.rect().center()).y(), table.mapTo(page, table.rect().bottomLeft()).y())

    def test_remove_action_tracks_cleared_and_removed_selection(self):
        page = self._show(QtPackageSettingsUI())
        table, remove = page.external_table, page.external_remove_button
        for clear in (table.clearSelection, table.clearContents, lambda: table.setRowCount(0)):
            page.add_external_row("C:/work/a.txt", "/a.txt")
            table.selectRow(table.rowCount() - 1)
            self.assertTrue(remove.isEnabled())
            clear()
            self.assertFalse(remove.isEnabled())

    def test_page_destruction_does_not_call_deleted_table_wrappers(self):
        # Qt's default exception hook aborts on an unsafe destruction callback.
        # Check a separate process so the regression cannot terminate this suite.
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "from PyQt6.QtWidgets import QApplication\n"
            "from PyQt6.QtCore import QCoreApplication, QEvent\n"
            "from UI.ui_package_settings import QtPackageSettingsUI\n"
            "app=QApplication([])\n"
            "for i in range(3):\n"
            " page=QtPackageSettingsUI(); page.show(); app.processEvents()\n"
            " page.deleteLater()\n"
            " QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)\n"
            "print('clean destruction')\n"
        )
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean destruction", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_start_is_accented_and_stopping_uses_a_plain_disabled_button(self):
        page = self._show(QtPackageProjectUI())
        requested = []
        page.packaging_requested.connect(lambda: requested.append(True))
        self.assertIsInstance(page.package_toggle_button, PrimaryPushButton)
        self.assertTrue(page.package_start_button.isVisible())
        self.assertFalse(page.package_stop_button.isVisible())
        page.package_toggle_button.click()

        page.set_packaging_action("Stop Packaging")
        self.assertNotIsInstance(page.package_toggle_button, PrimaryPushButton)
        self.assertFalse(page.package_start_button.isVisible())
        self.assertTrue(page.package_toggle_button.isEnabled())
        page.package_toggle_button.click()

        page.set_packaging_action("Stopping...")
        self.assertFalse(page.package_toggle_button.isEnabled())
        page.set_packaging_action("Start Packaging")
        self.assertTrue(page.package_toggle_button.isEnabled())
        self.assertEqual(len(requested), 2)

    def test_missing_summary_values_use_the_caution_colour(self):
        page = self._show(QtPackageProjectUI())
        output = page._summary_labels["output"]
        self.assertEqual(output.lightColor.name(), ls.CAUTION_TEXT_LIGHT)
        page.set_build_summary(output="C:/out")
        self.assertEqual(output.lightColor.name(), ls.DEFAULT_TEXT_LIGHT)

    def test_log_export_copy_clear_and_write_failure(self):
        page = self._show(QtPackageProjectUI())
        notifications = []
        page.notify.connect(lambda level, text: notifications.append((level, text)))
        payload = "编译日志 <b>plain text</b> & UTF-8\nSecond line"
        page.append_terminal(payload)
        self.assertEqual(page.package_terminal.toPlainText(), payload)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "构建 log.txt"
            with patch("UI.ui_package_project.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                page.save_log()
                self.assertEqual(output.read_bytes(), (payload + "\n").replace("\n", os.linesep).encode("utf-8"))
                with patch.object(Path, "write_text", side_effect=OSError("Read-only test destination")):
                    page.save_log()
                    self.assertEqual(notifications[-1][0], 0)
        clipboard = Mock()
        with patch("UI.ui_package_project.QApplication.clipboard", return_value=clipboard):
            page.copy_log()
            clipboard.setText.assert_called_once_with(payload)
        page.clear_log()
        self.assertTrue(page.package_terminal.document().isEmpty())
        self.assertTrue(all(not action.isEnabled() for action in page._log_actions))

    def test_update_button_follows_the_theme_colour(self):
        original = QColor(themeColor())
        self.addCleanup(setThemeColor, original)
        about = self._show(AboutUI())
        button = about.update_check_button
        button.clearFocus()
        for color in ("#107c10", "#c42b1c"):
            with self.subTest(color=color):
                setThemeColor(color)
                self.app.processEvents()
                image = button.grab().toImage()
                corner = image.pixelColor(round(4 * image.devicePixelRatio()), image.height() // 2)
                self.assertEqual(corner.name(), ThemeColor.PRIMARY.color().name())

    def test_return_button_is_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = QSettings(str(Path(folder) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainUI(AppPreferences(ROOT / "resource", storage, QLocale("en")))
            self.addCleanup(window.close)
            window.show()
            self.app.processEvents()
            self.assertFalse(window.navigationInterface.panel.returnButton.isVisible())
            # The first navigation item must stay below the title text instead of sliding under it.
            first = window.navigationInterface.widget(window.qt_package_settings.objectName())
            title = window.titleBar.titleLabel
            title_bottom = title.mapTo(window, title.rect().bottomLeft()).y()
            self.assertGreater(first.mapTo(window, first.rect().topLeft()).y(), title_bottom)
            icon = window.titleBar.iconLabel
            self.assertLess(icon.mapTo(window, icon.rect().topLeft()).x(), first.width() // 4)


if __name__ == "__main__":
    unittest.main()
