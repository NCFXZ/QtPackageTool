# SPDX-License-Identifier: GPL-3.0-or-later

import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from loguru import logger
from deployment.pe import read_pe_imports
from toolchains.discovery import qt_toolchain


QT_LIBRARY = "Qt Library"
QT_PLUGIN = "Qt Plugin"
QML_MODULE = "QML Module"


def qt_major_version(bin_path: Path) -> int:
    version = qt_toolchain(bin_path).version.partition(".")[0]
    if not version.isdecimal():
        raise ValueError(f"Could not determine Qt major version for {bin_path}")
    return int(version)


def qt_debug_runtime(bin_path: Path, is_release: bool) -> bool:
    return not is_release and (bin_path / f"Qt{qt_major_version(bin_path)}Cored.dll").is_file()


def runtime_binary(release: Path, debug: bool) -> Path:
    return release.with_stem(f"{release.stem}d") if debug else release


def _is_debug_binary(path: Path, major: int) -> bool:
    core_debug = f"qt{major}cored.dll"
    core_release = f"qt{major}core.dll"
    if path.name.casefold() == core_debug:
        return True
    try:
        _, imports = read_pe_imports(path)
    except (OSError, ValueError):
        return True
    lowered = {name.casefold() for name in imports}
    if core_debug in lowered or "ucrtbased.dll" in lowered:
        return True
    if core_release in lowered:
        return False
    return any(re.fullmatch(r"(?:vcruntime|msvcp|msvcr)\d+(?:_\d+)?d\.dll", name) for name in lowered)


@dataclass(frozen=True)
class QtDependency:
    name: str
    kind: str
    source: Path
    destination: str
    qml_import: str = ""
    companion: "QtDependency | None" = None

    def build_entries(self, debug: bool) -> list[dict]:
        source, destination = self.source, self.destination
        if not self.qml_import:
            source = runtime_binary(source, debug)
            destination = str(PurePosixPath(destination).with_name(source.name))
        entries = [{"Source": str(source), "Destination": destination, "Type": self.kind}]
        if self.qml_import:
            entries[0]["Import"] = self.qml_import
        if self.companion is not None:
            entries.extend(self.companion.build_entries(debug))
        return entries


def _binary_families(directory: Path, qt_bin: Path, major: int) -> list[Path]:
    files = {path.name.casefold(): path for path in directory.glob("*.dll") if path.is_file()}
    debug_only = not (qt_bin / f"Qt{major}Core.dll").is_file() and (
        qt_bin / f"Qt{major}Cored.dll"
    ).is_file()
    return [
        path.with_stem(path.stem[:-1])
        if debug_only and path.stem.endswith("d") and _is_debug_binary(path, major)
        else path
        for name, path in files.items()
        if not (name.endswith("d.dll") and (name[:-5] + ".dll") in files)
    ]


def _qml_dependencies(qml_root: Path, major: int) -> list[QtDependency]:
    found = []
    for qmldir in sorted(qml_root.rglob("qmldir")):
        try:
            declaration = qmldir.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            logger.warning(f"Could not read QML module {qmldir}: {exc}")
            continue
        module = re.search(r"(?m)^\s*module[ \t]+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)[ \t]*$", declaration)
        if not module:
            continue
        uri = module[1]
        relative = qmldir.parent.relative_to(qml_root).as_posix()
        version = re.search(r"\.(\d+(?:\.\d+)?)(?=/|$)", relative)
        imports = uri
        label = uri
        if version:
            label = f"{uri} ({relative})"
            number = version[1]
            imports += f" {number if '.' in number else number + '.0'}"
        elif major == 5:
            imports += " 1.0"
        dest = f"/qml/{relative}" if major >= 6 else f"/{relative}"
        found.append(QtDependency(label, QML_MODULE, qmldir.parent, dest, imports))
    return found


def discover_qt_dependencies(qt_bin: Path, companions: dict) -> dict[str, QtDependency]:
    major = qt_major_version(qt_bin)
    found = {}
    prefix = f"Qt{major}"
    for file in _binary_families(qt_bin, qt_bin, major):
        if file.stem.startswith(prefix):
            name = "Qt" + file.stem[len(prefix):]
            found[name] = QtDependency(name, QT_LIBRARY, file, f"/{file.name}")

    for directory in (qt_bin.parent / "plugins").glob("*"):
        if directory.name in {"designer", "qmltooling", "qmllint"}:
            continue
        for file in _binary_families(directory, qt_bin, major):
            target = f"{directory.name}/{file.name}"
            found[target] = QtDependency(target, QT_PLUGIN, file, f"/{target}")

    qml = {item.name: item for item in _qml_dependencies(qt_bin.parent / "qml", major)}
    for name, uri in companions.items():
        if name in found and isinstance(uri, str) and uri in qml:
            found[name] = replace(found[name], companion=qml[uri])
    for name, module in qml.items():
        if name in found:
            module = replace(module, name=f"{name} (QML)")
        found[module.name] = module
    return dict(sorted(found.items(), key=lambda item: item[0].casefold()))
