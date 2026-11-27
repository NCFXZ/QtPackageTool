# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QByteArray, QCoreApplication, QLocale, QMimeData, QSettings, QUrl
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentIconBase, Theme

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from UI.i18n import preferred_language, tr
from UI import ui_icons
from UI.ui_preferences import AppPreferences
from UI.ui_package_settings import local_paths, project_file
from deployment.qt_dependencies import QML_MODULE, QT_LIBRARY, QT_PLUGIN, discover_qt_dependencies, qt_debug_runtime, qt_major_version
from toolchains.discovery import Toolchain, discover_toolchains, matching_compiler


def write_tool_files(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True)
    for name in names:
        (directory / name).touch()


class PreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_system_language_and_saved_preferences(self):
        self.assertEqual(preferred_language(QLocale("zh_CN")), "zh_CN")
        self.assertEqual(preferred_language(QLocale("en_GB")), "en")
        self.assertEqual(preferred_language(QLocale("fr_FR")), "en")
        with tempfile.TemporaryDirectory() as folder:
            ini = str(Path(folder) / "settings.ini")
            settings = QSettings(ini, QSettings.Format.IniFormat)
            preferences = AppPreferences(ROOT / "resource", settings, QLocale("zh_CN"))
            self.addCleanup(preferences.select_language, "en")
            self.assertEqual(preferences.language, "zh_CN")
            self.assertEqual(preferences.theme, "system")
            self.assertEqual(tr("Settings"), "设置")
            self.assertIsNone(settings.value("language"))

            preferences.select_language("en")
            preferences.select_theme("dark")
            reopened = AppPreferences(
                ROOT / "resource", QSettings(ini, QSettings.Format.IniFormat), QLocale("zh_CN")
            )
            self.assertEqual(reopened.language, "en")
            self.assertEqual(reopened.theme, "dark")
            self.assertEqual(tr("Settings"), "Settings")


    def test_project_session_and_window_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            ini = str(Path(folder) / "settings.ini")
            preferences = AppPreferences(ROOT / "resource", QSettings(ini, QSettings.Format.IniFormat), QLocale("en"))
            self.assertEqual(preferences.project_session(), {})
            self.assertTrue(preferences.window_geometry().isEmpty())

            session = {"project": "C:/项目 1/app.pro", "release": False, "external": [["a.txt", "/a.txt"]]}
            preferences.save_project_session(session)
            preferences.save_window_geometry(QByteArray(b"geometry"))
            reopened = AppPreferences(ROOT / "resource", QSettings(ini, QSettings.Format.IniFormat), QLocale("en"))
            self.assertEqual(reopened.project_session(), session)
            self.assertEqual(bytes(reopened.window_geometry()), b"geometry")

            settings = QSettings(ini, QSettings.Format.IniFormat)
            settings.setValue("project/session", "{broken")
            settings.sync()
            self.assertEqual(AppPreferences(ROOT / "resource", settings, QLocale("en")).project_session(), {})


class DropPathTests(unittest.TestCase):
    @staticmethod
    def mime(*urls: QUrl) -> QMimeData:
        data = QMimeData()
        data.setUrls(list(urls))
        return data

    def test_accepts_only_local_paths_and_single_project(self):
        project = QUrl.fromLocalFile("C:/work/App.PRO")
        other = QUrl.fromLocalFile("C:/work/readme.md")
        remote = QUrl("https://example.com/app.pro")
        self.assertEqual(local_paths(self.mime(project, other)), ["C:/work/App.PRO", "C:/work/readme.md"])
        self.assertEqual(local_paths(self.mime(project, remote)), [])
        self.assertEqual(project_file(self.mime(project)), "C:/work/App.PRO")
        self.assertEqual(project_file(self.mime(project, other)), "")
        self.assertEqual(project_file(self.mime(other)), "")
        self.assertEqual(local_paths(QMimeData()), [])


class IconTests(unittest.TestCase):
    def test_distinct_functions_use_distinct_icons(self):
        icons = [icon for icon in vars(ui_icons).values() if isinstance(icon, FluentIconBase)]
        self.assertEqual(len(icons), len(set(icons)))
        paths = [icon.path(Theme.DARK) for icon in icons if isinstance(icon, FluentIcon)]
        self.assertEqual(len(paths), len(set(paths)))


class DependencyCatalogTests(unittest.TestCase):
    def test_selects_one_matching_compiler(self):
        kit = Toolchain(Path("qt/bin"), "x86_64", "11.2")
        incompatible = Toolchain(Path("A/bin"), "x86_64", "12.2")
        unversioned = Toolchain(Path("B/bin"), "x86_64")
        matching = Toolchain(Path("C/bin"), "x86_64", "11.2")
        wrong_arch = Toolchain(Path("D/bin"), "x86", "11.2")
        self.assertEqual(matching_compiler(kit, [incompatible, unversioned, matching, wrong_arch]), matching)
        self.assertIsNone(matching_compiler(kit, [incompatible, wrong_arch]))
        installed_kit = Toolchain(Path("Qt SDK/6.9.2/mingw_64/bin"), "x86_64", "11.2")
        bundled = Toolchain(Path("Qt SDK/Tools/mingw64/bin"), "x86_64", "11.2")
        self.assertEqual(matching_compiler(installed_kit, [matching, bundled]), bundled)

    def test_discovers_distinct_binaries_and_qml_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            kit = Path(folder) / "6.5.3" / "mingw_64"
            bin_path = kit / "bin"
            for relative in (
                "bin/Qt6Core.dll", "bin/Qt6Cored.dll",
                "bin/Qt6Multimedia.dll", "bin/Qt6Multimediad.dll",
                "bin/Qt6Gamepad.dll", "plugins/sqldrivers/qsqlite.dll",
                "plugins/sqldrivers/qsqlited.dll",
            ):
                file = kit / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            qmldir = kit / "qml" / "QtMultimedia" / "qmldir"
            qmldir.parent.mkdir(parents=True)
            qmldir.write_text("module QtMultimedia\n", encoding="utf-8")

            catalog = discover_qt_dependencies(bin_path, {"QtMultimedia": "QtMultimedia"})
            multimedia = catalog["QtMultimedia"]
            qml = catalog["QtMultimedia (QML)"]
            plugin = catalog["sqldrivers/qsqlite.dll"]
            self.assertEqual((multimedia.kind, qml.kind, plugin.kind), (QT_LIBRARY, QML_MODULE, QT_PLUGIN))
            self.assertIn("QtGamepad", catalog)
            self.assertNotIn("QtMultimediad", catalog)
            self.assertTrue(qt_debug_runtime(bin_path, False))
            self.assertEqual(multimedia.build_entries(False)[0]["Destination"], "/Qt6Multimedia.dll")
            self.assertEqual(multimedia.build_entries(True)[0]["Destination"], "/Qt6Multimediad.dll")
            self.assertEqual(multimedia.build_entries(True)[1]["Import"], "QtMultimedia")
            self.assertEqual(plugin.build_entries(True)[0]["Destination"], "/sqldrivers/qsqlited.dll")

    def test_discovers_qt_and_mingw_from_separate_environment_locations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            kit = root / "Development kits" / "Desktop SDK"
            qt_bin = kit / "bin"
            mingw = root / "Standalone compiler"
            mingw_bin = mingw / "bin"
            write_tool_files(qt_bin, "qmake.exe", "windeployqt.exe", "Qt6Svg.dll")
            write_tool_files(mingw_bin, "g++.exe", "mingw32-make.exe")
            (mingw / "lib/gcc/x86_64-w64-mingw32/14.2.0").mkdir(parents=True)
            (kit / "mkspecs").mkdir()
            (kit / "mkspecs/qconfig.pri").write_text(
                "QT_VERSION = 6.9.2\nQT_ARCH = x86_64\nQT_GCC_MAJOR_VERSION = 14\nQT_GCC_MINOR_VERSION = 2\n",
                encoding="utf-8",
            )

            with (
                patch("toolchains.discovery._common_qt_locations", return_value=[]),
                patch.dict(os.environ, {"QTDIR": str(kit), "PATH": f'"{mingw_bin}"'}, clear=True),
            ):
                kits, compilers = discover_toolchains()
                browsed, _ = discover_toolchains(str(qt_bin))

            actual = kits["Qt 6.9.2 (Desktop SDK)"]
            self.assertEqual(actual.directory, qt_bin)
            self.assertEqual(browsed, kits)
            self.assertEqual(matching_compiler(actual, compilers).directory, mingw_bin)
            self.assertIn("QtSvg", discover_qt_dependencies(actual.directory, {}))

    def test_creator_hint_and_version_directory_both_find_their_compiler(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            location = root / "Qt SDK"
            version = location / "5.15.8"
            qt_bin = version / "mingw_32" / "bin"
            mingw_bin = location / "Tools" / "mingw32" / "bin"
            write_tool_files(qt_bin, "qmake.exe", "windeployqt.exe")
            write_tool_files(mingw_bin, "g++.exe", "mingw32-make.exe")
            (mingw_bin.parent / "lib/gcc/i686-w64-mingw32/10.3.0").mkdir(parents=True)
            profile = root / "Profile"
            settings = profile / "QtProject" / "qtcreator"
            settings.mkdir(parents=True)
            (settings / "qtversion.xml").write_text(
                f'<qtcreator><data><value key="QMakePath">{qt_bin.as_posix()}/qmake.exe</value></data></qtcreator>',
                encoding="utf-8",
            )
            (settings / "toolchains.xml").write_text("invalid xml", encoding="utf-8")
            with (
                patch("toolchains.discovery._common_qt_locations", return_value=[]),
                patch("toolchains.discovery.logger.warning") as warning,
                patch.dict(os.environ, {"APPDATA": str(profile)}, clear=True),
            ):
                for selected in (None, str(location), str(version), str(qt_bin.parent)):
                    kits, compilers = discover_toolchains(selected)
                    self.assertEqual(list(kits), ["Qt 5.15.8 (mingw_32)"])
                    self.assertEqual(matching_compiler(next(iter(kits.values())), compilers).directory, mingw_bin)
                warning.assert_called()

    def test_qt_major_version_raises_value_error_when_version_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            bin_path = Path(folder) / "custom" / "kit" / "bin"
            bin_path.mkdir(parents=True)
            with self.assertRaises(ValueError):
                qt_major_version(bin_path)

    def test_debug_only_kit_does_not_truncate_release_dll_ending_in_d(self):
        with tempfile.TemporaryDirectory() as folder:
            kit = Path(folder) / "6.5.3" / "mingw_64"
            bin_path = kit / "bin"
            bin_path.mkdir(parents=True)
            for name in ("Qt6Cored.dll", "Qt6VirtualKeyboardd.dll", "Qt6Gamepad.dll"):
                (bin_path / name).touch()

            def fake_imports(binary: Path):
                if binary.name == "Qt6VirtualKeyboardd.dll":
                    return (0x8664, ["Qt6Cored.dll"])
                if binary.name == "Qt6Gamepad.dll":
                    return (0x8664, ["Qt6Core.dll"])
                return (0x8664, [])

            with patch("deployment.qt_dependencies.read_pe_imports", side_effect=fake_imports):
                catalog = discover_qt_dependencies(bin_path, {})

            self.assertIn("QtCore", catalog)
            self.assertIn("QtVirtualKeyboard", catalog)
            self.assertIn("QtGamepad", catalog)
            self.assertNotIn("QtGamepa", catalog)
