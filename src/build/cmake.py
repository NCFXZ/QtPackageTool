# SPDX-License-Identifier: GPL-3.0-or-later

"""CMake file API replies and target selection independent of the process runner."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from toolchains.models import MSVC, Toolchain

from .paths import deployment_path


CMAKE_BUILD_DIR = ".qt-cmake"
CMAKE_PACKAGE_DIR = ".qt-package"
CMAKE_PROJECT = "cmakelists.txt"


@dataclass(frozen=True)
class CMakeTargets:
    target: str
    build_targets: list[str]
    executable: Path
    shared_libraries: list[Path]
    module_libraries: list[Path]


def is_cmake_project(path: Path) -> bool:
    return path.name.casefold() == CMAKE_PROJECT


def configure_arguments(
    project: Path, build_dir: Path, qt_bin: Path, compiler: Toolchain,
    is_release: bool, ninja: Path | None,
) -> list[str]:
    query = build_dir / ".cmake" / "api" / "v1" / "query" / "codemodel-v2"
    query.parent.mkdir(parents=True)
    query.touch()

    if compiler.kind == MSVC:
        compilers = ("cl.exe", "cl.exe")
        generator = "NMake Makefiles"
        make_program = None
    else:
        compilers = (
            (compiler.directory / "gcc.exe").as_posix(),
            (compiler.directory / "g++.exe").as_posix(),
        )
        generator = "MinGW Makefiles"
        make_program = compiler.directory / "mingw32-make.exe"
    if ninja is not None:
        generator, make_program = "Ninja", ninja
    args = [
        "-S", str(project.parent),
        "-B", str(build_dir),
        "-G", generator,
        f"-DCMAKE_BUILD_TYPE={'Release' if is_release else 'Debug'}",
        f"-DCMAKE_PREFIX_PATH={qt_bin.parent.as_posix()}",
        f"-DCMAKE_C_COMPILER={compilers[0]}",
        f"-DCMAKE_CXX_COMPILER={compilers[1]}",
    ]
    if make_program is not None:
        args.append(f"-DCMAKE_MAKE_PROGRAM={Path(make_program).as_posix()}")
    return args


def relative_path(root: Path, raw_path: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path or raw_path.startswith(("/", "\\")):
        raise ValueError(f"CMake path is outside the build directory: {raw_path}")
    return deployment_path(root, raw_path)


def artifact_path(build_dir: Path, raw_path: str) -> Path:
    # Absolute CMake runtime output directories may place artifacts outside the build tree.
    if isinstance(raw_path, str) and Path(raw_path).is_absolute():
        return Path(os.path.normpath(raw_path))
    return relative_path(build_dir, raw_path)


def read_codemodel(build_dir: Path, is_release: bool) -> CMakeTargets:
    """
    Read the CMake file API reply and return the executable to package with the
    project's shared libraries and module plugins.
    """
    reply = build_dir / ".cmake" / "api" / "v1" / "reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise ValueError("CMake did not write a file API reply.")
    index = json.loads(indexes[-1].read_text(encoding="utf-8"))
    codemodel_file = next(
        (item["jsonFile"] for item in index.get("objects", []) if item.get("kind") == "codemodel"), None
    )
    if codemodel_file is None:
        raise ValueError("CMake did not report the project targets.")
    codemodel = json.loads(relative_path(reply, codemodel_file).read_text(encoding="utf-8"))
    build_type = "Release" if is_release else "Debug"
    configurations = codemodel.get("configurations", [])
    configuration = next(
        (item for item in configurations if item.get("name") == build_type),
        configurations[0] if configurations else None,
    )
    if configuration is None:
        raise ValueError("CMake did not report a build configuration.")

    executables, shared_libraries, module_libraries, module_targets = {}, [], [], []
    for entry in configuration.get("targets", []):
        target_file = relative_path(reply, entry.get("jsonFile", ""))
        target = json.loads(target_file.read_text(encoding="utf-8"))
        artifacts = [
            artifact_path(build_dir, item.get("path", ""))
            for item in target.get("artifacts", [])
        ]
        target_type = target.get("type")
        if target_type == "EXECUTABLE":
            executable = next((path for path in artifacts if path.suffix.casefold() == ".exe"), None)
            if executable is not None:
                executables[target["name"]] = executable
        elif target_type == "SHARED_LIBRARY":
            shared_libraries.extend(path for path in artifacts if path.suffix.casefold() == ".dll")
        elif target_type == "MODULE_LIBRARY":
            dlls = [path for path in artifacts if path.suffix.casefold() == ".dll"]
            if dlls:
                module_libraries.extend(dlls)
                if target.get("name"):
                    module_targets.append(target["name"])

    if len(executables) > 1:
        projects = configuration.get("projects", [])
        root_names = {project.get("name") for project in projects if "parentIndex" not in project}
        preferred = {name: path for name, path in executables.items() if name in root_names}
        if len(preferred) != 1:
            all_names = {project.get("name") for project in projects}
            preferred = {name: path for name, path in executables.items() if name in all_names}
        if len(preferred) != 1:
            raise ValueError(
                "The CMake project has several executables. "
                f"Name one after the project to package it: {', '.join(sorted(executables))}"
            )
        executables = preferred
    if not executables:
        raise ValueError("The CMake project does not define an executable target.")
    target_name, executable = next(iter(executables.items()))
    build_targets = list(dict.fromkeys((target_name, *module_targets)))
    return CMakeTargets(target_name, build_targets, executable, shared_libraries, module_libraries)
