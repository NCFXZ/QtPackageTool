# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep build helpers and application state independent of the UI layer."""

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def _check_imports(self, imports: str, forbidden: tuple[str, ...]) -> None:
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            f"{imports}\n"
            f"for prefix in {forbidden!r}:\n"
            "    loaded = [name for name in sys.modules "
            "if name == prefix or name.startswith(prefix + '.')]\n"
            "    assert not loaded, (prefix, loaded)\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_build_helpers_and_models_do_not_load_qt_or_platform_services(self):
        self._check_imports(
            "import deployment.pe, toolchains.models\nfrom build import cmake, paths, qmake, qml",
            ("UI", "PyQt6", "qfluentwidgets", "win32api", "toolchains.discovery", "deployment.runtime_dependencies"),
        )

    def test_compiler_and_preferences_do_not_load_the_ui(self):
        self._check_imports(
            "import build.compiler, app.preferences, app.localization, app.runtime, deployment.qt_dependencies",
            ("UI", "PyQt6.QtWidgets", "qfluentwidgets", "qframelesswindow"),
        )

    def test_resource_root_after_moving_runtime_into_app_package(self):
        runtime_path = ROOT / "src" / "app" / "runtime.py"
        source = runtime_path.read_bytes()
        for compiled in (False, True):
            with self.subTest(compiled=compiled):
                app_dir = ROOT / "logs" / "simulated-dist" if compiled else ROOT
                module_path = app_dir / "app" / "runtime.py" if compiled else runtime_path
                namespace = {"__file__": str(module_path)}
                with patch.dict(sys.modules, {"__main__": SimpleNamespace(__compiled__=compiled)}):
                    exec(compile(source, str(module_path), "exec"), namespace)
                self.assertEqual(namespace["APP_DIR"], app_dir)
                self.assertEqual(namespace["RESOURCE_DIR"], app_dir / "resource")
