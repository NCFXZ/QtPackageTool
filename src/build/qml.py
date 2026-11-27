# SPDX-License-Identifier: GPL-3.0-or-later

"""Discover QML sources and prepare filtered input for Qt deployment tools."""

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path
from xml.etree import ElementTree

from .paths import deployment_path, entry_status


QML_SOURCE_SUFFIXES = {".qml", ".js", ".mjs"}
QML_SCAN_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".vs", ".vscode", ".idea",
}
QML_SOURCE_COPY = ".qt-qml-sources"


def read_resource_sources(project_dir: Path, collections: Iterable[str], check_cancelled: Callable[[], None]) -> set[Path]:
    sources = set()
    for entry in collections:
        check_cancelled()
        collection = project_dir / entry
        if collection.suffix.casefold() != ".qrc" or not collection.is_file():
            continue
        for file in ElementTree.parse(collection).findall("./qresource/file"):
            if file.text:
                source = collection.parent / file.text.strip()
                if source.suffix.casefold() in QML_SOURCE_SUFFIXES:
                    sources.add(source.resolve())
    return sources


def list_sources(
    project_dir: Path, output: Path, resource_sources: Iterable[Path], check_cancelled: Callable[[], None]
) -> list[Path]:
    """List the project's QML and script sources, leaving out generated packages and build trees."""
    sources = set(resource_sources)
    runtime_markers = ("Qt5Core.dll", "Qt5Cored.dll", "Qt6Core.dll", "Qt6Cored.dll")
    for folder, dirs, files in os.walk(project_dir):
        check_cancelled()
        directory = Path(folder)
        for name in dirs[:]:
            child = directory / name
            try:
                entry_status(child)
            except (OSError, ValueError):
                dirs.remove(name)
                continue
            if (
                child == output
                or name.startswith(".qt-build-")
                or name.casefold() in QML_SCAN_SKIP_DIRS
                or (child / "CMakeCache.txt").is_file()
                or (child / ".qmake.stash").is_file()
                or any((child / dll).is_file() for dll in runtime_markers)
            ):
                dirs.remove(name)
        for name in files:
            path = directory / name
            if path.suffix.casefold() in QML_SOURCE_SUFFIXES:
                sources.add(path)
    return sorted(sources)


def copy_sources(
    source_files: Iterable[Path], build_root: Path, manual_qml_file: Path | None, check_cancelled: Callable[[], None]
) -> Path | None:
    """
    Copy the listed QML sources for windeployqt, which would otherwise also scan
    packages and build trees inside the project folder.
    """
    sources = [path for path in source_files if path != manual_qml_file]
    if not sources:
        return None
    copy_root = deployment_path(build_root, QML_SOURCE_COPY)
    by_drive = {}
    for source in sources:
        by_drive.setdefault(source.drive.casefold(), []).append(source)
    for index, files in enumerate(by_drive.values()):
        # Keep the relative layout and qmldir files so directory imports still resolve.
        base = Path(os.path.commonpath([file.parent for file in files]))
        qmldirs = {file.parent / "qmldir" for file in files}
        for file in (*files, *(path for path in sorted(qmldirs) if path.is_file())):
            check_cancelled()
            target = deployment_path(copy_root, f"{index}/{file.relative_to(base).as_posix()}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
    return copy_root


def scan_arguments(source_files: Iterable[Path], build_root: Path) -> list[str]:
    qrc = deployment_path(build_root, ".qt-qml-check.qrc")
    if not qrc.is_file():
        resource_tree = ElementTree.Element("RCC")
        resource = ElementTree.SubElement(resource_tree, "qresource")
        for index, path in enumerate(source_files):
            ElementTree.SubElement(resource, "file", alias=f"{index}/{path.name}").text = path.as_posix()
        ElementTree.ElementTree(resource_tree).write(qrc, encoding="utf-8", xml_declaration=True)
    return ["-qrcFiles", str(qrc)]


def import_directories(project_dir: Path, import_paths: Iterable[str], environment: dict[str, str]) -> list[str]:
    imports = list(import_paths)
    for variable in ("QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
        imports.extend(environment.get(variable, "").split(os.pathsep))
    directories = dict.fromkeys(
        Path(os.path.abspath(project_dir / value))
        for entry in imports
        if (value := entry.strip().strip('"'))
    )
    return [str(directory) for directory in directories if directory.is_dir()]
