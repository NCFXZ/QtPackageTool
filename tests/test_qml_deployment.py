# SPDX-License-Identifier: GPL-3.0-or-later

import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build.compiler import QtCompiler
from deployment.runtime_dependencies import check_qml_modules, qml_runtime_dirs, read_pe_imports


class QmlDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_project_resource_outside_source_is_included_in_the_scan(self):
        project = self.root / "project"
        shared = self.root / "shared source"
        output = project / "output"
        staging = output / ".qt-build-test"
        shared.mkdir()
        staging.mkdir(parents=True)
        source = shared / "External.qml"
        source.write_text("import QtQuick\nItem {}\n", encoding="ascii")
        (shared / "files.qrc").write_text(
            '<RCC><qresource><file>External.qml</file></qresource></RCC>',
            encoding="ascii",
        )
        (output / "Old.qml").write_text("import Stale.Module\n", encoding="ascii")

        compiler = QtCompiler()
        compiler.output_path = str(output)
        compiler._build_root = staging
        compiler._resource_collections = ["../shared source/files.qrc"]
        compiler._qml_resource_sources = compiler._read_qml_resource_sources(project)
        self.assertEqual(compiler._qml_resource_sources, {source})
        _, manifest = compiler._qml_check_sources(project)
        self.assertEqual(
            {Path(file.text) for file in ElementTree.parse(manifest).findall("./qresource/file")},
            {source},
        )

    def test_qml_import_paths_follow_the_packaged_qt_version_and_prefix(self):
        qml = self.root / "qml"
        legacy = self.root / "legacy"
        current = self.root / "content" / "modules"
        for directory in (qml, legacy, current):
            directory.mkdir(parents=True)
        (self.root / "qt.conf").write_text("[Paths]\nQml2Imports = legacy\n", encoding="ascii")
        (self.root / "qt5.conf").write_text("[Paths]\nQml2Imports = qml\n", encoding="ascii")
        (self.root / "qt6.conf").write_text(
            "[Paths]\nPrefix = content\nQmlImports = modules\n", encoding="ascii"
        )

        self.assertEqual(qml_runtime_dirs(self.root, 5), [self.root, legacy])
        self.assertEqual(qml_runtime_dirs(self.root, 6), [self.root, current])

    def test_qml_plugin_must_exist_at_its_declared_location(self):
        module = self.root / "qml" / "Example"
        native = module / "native"
        native.mkdir(parents=True)
        (module / "qmldir").write_text(
            "module Example\nplugin example native\noptional plugin extra\n",
            encoding="ascii",
        )
        imports = [{"name": "Example", "path": str(module)}]
        with self.assertRaisesRegex(ValueError, "Required QML plugin example"):
            check_qml_modules(imports, self.root, False, lambda: None)

        plugin = native / "example.dll"
        plugin.touch()
        optional, plugins = check_qml_modules(imports, self.root, False, lambda: None)
        self.assertEqual(plugins, {plugin})
        self.assertEqual(optional, set())

    def test_qml_module_outside_package_is_rejected(self):
        package = self.root / "pkg"
        outside = self.root / "outside_sdk" / "QtQuick"
        package.mkdir()
        outside.mkdir(parents=True)
        (outside / "qmldir").write_text("module QtQuick\n", encoding="ascii")
        imports = [{"name": "QtQuick", "path": str(outside)}]
        with self.assertRaisesRegex(ValueError, "Required QML modules are unavailable: QtQuick"):
            check_qml_modules(imports, package, False, lambda: None)

    def test_qml_scan_skips_vcs_node_modules_and_nested_build_dirs(self):
        project = self.root / "project"
        output = self.root / "out"
        staging = output / ".qt-build-test"
        staging.mkdir(parents=True)
        good = project / "Main.qml"
        good.parent.mkdir(parents=True)
        good.write_text("import QtQuick\nItem {}\n", encoding="ascii")
        for skipped, marker in (
            (project / ".git", None),
            (project / "node_modules", None),
            (project / "build-desktop", "CMakeCache.txt"),
            (project / "qmake-build", ".qmake.stash"),
        ):
            skipped.mkdir(parents=True)
            if marker:
                (skipped / marker).write_text("", encoding="ascii")
            (skipped / "ignored.js").write_text("import broken;\n", encoding="ascii")

        compiler = QtCompiler()
        compiler.output_path = str(output)
        compiler._build_root = staging
        _, manifest = compiler._qml_check_sources(project)
        self.assertEqual(
            {Path(file.text) for file in ElementTree.parse(manifest).findall("./qresource/file")},
            {good},
        )

    def test_windeployqt_gets_a_copy_of_sources_without_nested_packages(self):
        project = self.root / "project"
        output = project / "out"
        staging = output / ".qt-build-test"
        staging.mkdir(parents=True)
        for relative, text in (
            ("Main.qml", "import QtQuick\nItem {}\n"),
            ("ui/Page.qml", 'import "../shared"\nItem {}\n'),
            ("ui/qmldir", "Page 1.0 Page.qml\n"),
            ("out/release/qml/QtQuick/Controls/Material/Button.qml", "import QtQuick.Controls.Material.impl\n"),
            ("legacy-package/Qt6Core.dll", ""),
            ("legacy-package/Old.qml", "import Stale.Module\n"),
        ):
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="ascii")

        compiler = QtCompiler()
        compiler.output_path = str(output)
        compiler._build_root = staging
        copy = compiler._copy_qml_sources(project)
        self.assertEqual(
            sorted(path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file()),
            ["0/Main.qml", "0/ui/Page.qml", "0/ui/qmldir"],
        )

    def test_pe_imports_handles_tight_import_size_without_null_descriptor(self):
        # Build a minimal valid PE32+ image whose import directory size is exactly 20 bytes (1 descriptor, no null tail).
        buf = bytearray(0x600)
        buf[0:2] = b"MZ"
        pe_offset = 0x80
        struct.pack_into("<I", buf, 0x3C, pe_offset)
        buf[pe_offset:pe_offset + 4] = b"PE\0\0"
        optional_size = 240
        struct.pack_into("<HHIIIHH", buf, pe_offset + 4, 0x8664, 1, 0, 0, 0, optional_size, 0)
        optional = pe_offset + 24
        struct.pack_into("<H", buf, optional, 0x20B)  # PE32+
        struct.pack_into("<I", buf, optional + 108, 16)  # NumberOfRvaAndSizes
        import_rva = 0x1000
        struct.pack_into("<II", buf, optional + 112 + 8, import_rva, 20)  # Exact 1-descriptor size
        section_table = optional + optional_size
        struct.pack_into("<III", buf, section_table + 12, 0x1000, 0x200, 0x200)  # VA, SizeOfRawData, PtrToRawData
        name_rva = 0x1040
        struct.pack_into("<5I", buf, 0x200, 0, 0, 0, name_rva, 0x1080)
        dll_name = b"KERNEL32.dll\0"
        buf[0x240:0x240 + len(dll_name)] = dll_name

        binary = self.root / "tight.exe"
        binary.write_bytes(buf)
        self.assertEqual(read_pe_imports(binary), (0x8664, ["KERNEL32.dll"]))
