# Qt Package Tool - A PyQt6-based application
# Copyright (c) 2025 NCFXZ
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import json
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

import win32api
from PyQt6.QtCore import QSettings

from deployment.pe import _pe_imports, read_pe_imports


def _is_reparse_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0) & 0x20000000)


def validate_startup_libraries(
    executable: Path,
    check_cancelled: Callable[[], None],
    plugins: Iterable[Path] = (),
    libraries: Iterable[Path] = (),
) -> None:
    """Check load-time imports using the package and Windows system folders."""
    package = executable.parent.resolve()
    machine, imports = read_pe_imports(executable)
    windows = Path(win32api.GetWindowsDirectory())
    system = windows / "System32"
    if machine == 0x14C and (windows / "SysWOW64").is_dir():
        system = windows / "SysWOW64"
    elif machine != 0x14C and (windows / "Sysnative").is_dir():
        system = windows / "Sysnative"

    pending = [(executable, imports)]
    seen = {executable}
    for kind, binaries in (("QML plugin", plugins), ("Qt dependency", libraries)):
        for binary in binaries:
            check_cancelled()
            if binary in seen:
                continue
            actual_machine, imports = read_pe_imports(binary)
            if actual_machine != machine:
                raise ValueError(f"{kind} {binary.name} has a different architecture from {executable.name}.")
            seen.add(binary)
            pending.append((binary, imports))
    missing = {}
    while pending:
        check_cancelled()
        binary, dependencies = pending.pop()
        for name in dependencies:
            check_cancelled()
            relative = Path(name)
            if relative.name != name or relative.anchor or ":" in name:
                raise ValueError(f"Invalid imported library name in {binary.name}: {name}")
            key = name.casefold()
            if key.startswith(("api-ms-win-", "ext-ms-win-")):
                continue
            local = package / name
            if local in seen:
                continue
            seen.add(local)
            if local.is_file():
                if not local.resolve().is_relative_to(package):
                    raise ValueError(f"Imported library points outside the package: {name}")
                actual_machine, local_imports = read_pe_imports(local)
                if actual_machine != machine:
                    raise ValueError(f"Imported library {name} has a different architecture from {executable.name}.")
                pending.append((local, local_imports))
            elif not (system / name).is_file() and not (windows / name).is_file():
                missing[key] = f"{name} (required by {binary.name})"

    if missing:
        details = ", ".join(missing[key] for key in sorted(missing))
        raise ValueError(f"Required DLLs missing from the package and Windows system folders: {details}")


def read_qml_modules(output: str) -> list[dict]:
    entries = json.loads(output)
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("Unexpected output from qmlimportscanner.")
    return [entry for entry in entries if entry.get("type") == "module" and entry.get("name")]


def qml_runtime_dirs(package: Path, qt_major: int) -> list[Path]:
    """Return application-local QML import paths in runtime search order."""
    package_root = package.resolve()
    prefix = package_root
    imports = ["qml"]
    filenames = (f"qt{qt_major}.conf", "qt.conf") if qt_major >= 6 else ("qt.conf",)
    for filename in filenames:
        config = package_root / filename
        if not config.is_file():
            continue
        settings = QSettings(str(config), QSettings.Format.IniFormat)
        prefix /= settings.value("Paths/Prefix", ".", type=str)
        legacy = settings.value("Paths/Qml2Imports", "qml")
        value = settings.value("Paths/QmlImports", legacy) if qt_major >= 6 else legacy
        imports = value if isinstance(value, list) else [value]
        break

    directories = dict.fromkeys([package_root, *((prefix / entry).resolve() for entry in imports)])
    return [path for path in directories if path.is_relative_to(package_root) and path.is_dir()]


def check_qml_modules(
    modules: list[dict],
    package: Path,
    debug: bool,
    check_cancelled: Callable[[], None],
) -> tuple[set[str], set[Path]]:
    package_root = package.resolve()
    missing = {module["name"] for module in modules if not module.get("path")}
    required = set()
    optional = set()
    plugins = set()
    for module in modules:
        check_cancelled()
        if not module.get("path"):
            continue
        module_path = Path(module["path"])
        try:
            if _is_reparse_link(module_path):
                missing.add(module["name"])
                continue
            resolved_module = module_path.resolve(strict=True)
        except OSError:
            missing.add(module["name"])
            continue
        if not resolved_module.is_relative_to(package_root):
            missing.add(module["name"])
            continue
        qmldir = resolved_module / "qmldir"
        if not qmldir.is_file():
            continue
        try:
            if _is_reparse_link(qmldir) or not qmldir.resolve(strict=True).is_relative_to(package_root):
                missing.add(module["name"])
                continue
        except OSError:
            missing.add(module["name"])
            continue
        for line in qmldir.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            match line.partition("#")[0].split():
                case ["optional", "import", name, *_]:
                    optional.add(name)
                case ["default", "import", name, *_] | ["import" | "depends", name, *_]:
                    required.add(name)
                case ["plugin", name, *location]:
                    directory = qmldir.parent / (location[0] if location else ".")
                    names = [f"{name}d.dll", f"{name}.dll"] if debug else [f"{name}.dll"]
                    for filename in names:
                        candidate = directory / filename
                        if not candidate.is_file():
                            continue
                        try:
                            if _is_reparse_link(candidate):
                                continue
                            plugin = candidate.resolve(strict=True)
                        except OSError:
                            continue
                        if plugin.is_relative_to(package_root) and plugin.is_file():
                            plugins.add(plugin)
                            break
                    else:
                        raise ValueError(f"Required QML plugin {name} is missing for {module['name']}.")

    optional_missing = missing & (optional - required)
    if missing_required := missing - optional_missing:
        raise ValueError(f"Required QML modules are unavailable: {', '.join(sorted(missing_required))}")
    return optional_missing, plugins
