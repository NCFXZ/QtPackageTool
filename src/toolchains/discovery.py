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
import os
import platform
import re
import stat
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import asdict
from itertools import chain
from pathlib import Path
from xml.etree import ElementTree

import win32api
import win32con
import win32file
from loguru import logger
from PyQt6.QtCore import QDir

from toolchains.models import MINGW, MSVC, Toolchain, matching_compiler, version_key as _version


QT_VERSION = re.compile(r"^\d+\.\d+")
# Architecture names used by vcvarsall.bat and the MSVC Host<host>/<target> folders.
VCVARS_NAMES = {"x86_64": "x64", "x86": "x86", "arm64": "arm64"}
KIT_PROGRAMS = ("qmake.exe", "windeployqt.exe")
MINGW_PROGRAMS = ("g++.exe", "mingw32-make.exe")
_UNSAFE_CMD_CHARS = set('%!"\r\n\x00')


def host_architecture() -> str:
    machine = (os.environ.get("PROCESSOR_ARCHITEW6432") or platform.machine()).casefold()
    return "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("amd64", "x86_64") else "x86"


def _host_candidates() -> tuple[str, ...]:
    host = host_architecture()
    if host == "arm64":
        return ("arm64", "x86_64", "x86")
    if host == "x86_64":
        return ("x86_64", "x86")
    return ("x86",)


def _architecture(name: str) -> str:
    if name.casefold().endswith(("arm64", "aarch64")):
        return "arm64"
    if name.endswith("_64") or name == "x86_64":
        return "x86_64"
    if re.fullmatch(r"i[3-6]86", name) or name.endswith("_32"):
        return "x86"
    return name if name == "x86" else ""


def qt_toolchain(directory: Path) -> Toolchain:
    kit = directory.parent
    config = kit / "mkspecs" / "qconfig.pri"
    info = {}
    if config.is_file():
        contents = config.read_text(encoding="utf-8-sig", errors="replace")
        info = dict(
            re.findall(r"(?m)^(QT_\w+)[ \t]*=[ \t]*(\S+)", contents)
        )
    arch = _architecture(info.get("QT_ARCH", kit.name))
    major, minor = info.get("QT_GCC_MAJOR_VERSION", ""), info.get("QT_GCC_MINOR_VERSION", "")
    gcc = f"{major}.{minor}" if major.isdecimal() and minor.isdecimal() else ""
    version = info.get("QT_VERSION", kit.parent.name)
    version = version if QT_VERSION.match(version) else ""
    cl_major, cl_minor = info.get("QT_MSVC_MAJOR_VERSION", ""), info.get("QT_MSVC_MINOR_VERSION", "")
    if kit.name.casefold().startswith(MSVC) or (cl_major.isdecimal() and not gcc):
        cl_major = cl_major if cl_major.isdecimal() else "19"
        msvc = f"{cl_major}.{cl_minor}" if cl_minor.isdecimal() else cl_major
        return Toolchain(directory, arch, version=version, kind=MSVC, msvc=msvc)
    return Toolchain(directory, arch, gcc, version)


def mingw_toolchain(directory: Path) -> Toolchain:
    installation = directory.parent
    targets = {
        target: _architecture(target.name.partition("-")[0])
        for target in (installation / "lib" / "gcc").glob("*-mingw32")
        if target.is_dir()
    }
    arches = set(targets.values()) - {""}
    arch = next(iter(arches)) if len(arches) == 1 else _architecture(installation.name)
    versions = {
        match[0]
        for target, target_arch in targets.items()
        if target_arch == arch
        for version in target.iterdir()
        if version.is_dir() and (match := re.match(r"\d+\.\d+(?=\.|$)", version.name))
    }
    gcc = next(iter(versions)) if len(versions) == 1 else ""
    return Toolchain(directory, arch, gcc)


def _vswhere_installations() -> list[tuple[Path, str]]:
    for program_files in _environment_paths("ProgramFiles(x86)", "ProgramFiles"):
        vswhere = program_files / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if not vswhere.is_file():
            continue
        try:
            result = subprocess.run(
                [str(vswhere), "-all", "-products", "*", "-format", "json", "-utf8"],
                capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return [
                (path, entry.get("displayName", ""))
                for entry in json.loads(result.stdout.decode("utf-8") or "[]")
                if (path := _absolute_path(entry.get("installationPath", "")))
            ]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning(f"Could not query Visual Studio installations: {exc}")
    return []


def _visual_studio_installations() -> list[tuple[Path, str]]:
    installations = dict(_vswhere_installations())
    for program_files in _environment_paths("ProgramFiles", "ProgramFiles(x86)"):
        for year in _children(program_files / "Microsoft Visual Studio"):
            for edition in _children(year):
                installations.setdefault(edition, f"Visual Studio {year.name} {edition.name}")
    return list(installations.items())


def _is_reparse_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0) & 0x20000000)


def _system_cmd() -> Path:
    cmd = Path(win32api.GetSystemDirectory()) / "cmd.exe"
    if not cmd.is_file():
        raise FileNotFoundError(f"cmd.exe not found: {cmd}")
    return cmd.resolve(strict=True)


def _checked_vcvars(compiler: Toolchain) -> Path:
    raw_dir = compiler.directory
    raw_vcvars = raw_dir / "vcvarsall.bat"
    if not raw_vcvars.is_file():
        raise FileNotFoundError(f"vcvarsall.bat not found: {raw_vcvars}")
    if _is_reparse_link(raw_vcvars):
        raise ValueError(f"Linked vcvarsall.bat is not allowed: {raw_vcvars}")
    directory = raw_dir.resolve(strict=True)
    vcvars = raw_vcvars.resolve(strict=True)
    if vcvars.parent != directory:
        raise ValueError(f"vcvarsall.bat is outside the selected Visual Studio directory: {raw_vcvars}")
    if any(ch in str(vcvars) for ch in _UNSAFE_CMD_CHARS):
        raise ValueError(f"Unsafe characters in vcvarsall.bat path: {vcvars}")
    return vcvars


def msvc_toolchains(installations: Iterable[tuple[Path, str]] | None = None) -> list[Toolchain]:
    """List the newest MSVC compiler of each Visual Studio installation for every target architecture."""
    hosts = _host_candidates()
    compilers: dict[tuple[Path, str], tuple[tuple[int, ...], int, Toolchain]] = {}
    if installations is None:
        installations = _visual_studio_installations()
    for installation, display_name in installations:
        vcvars = installation / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
        if not vcvars.is_file():
            continue
        for tools in _children(installation / "VC" / "Tools" / "MSVC"):
            toolset = _version(tools.name)
            if len(toolset) < 2:
                continue
            # Toolset 14.x ships cl.exe 19.x.
            cl_version = f"{toolset[0] + 5}.{toolset[1]}"
            toolset_version = f"{toolset[0]}.{toolset[1]}"
            for arch, target in VCVARS_NAMES.items():
                matched_host = next(
                    (
                        (rank, host_arch)
                        for rank, host_arch in enumerate(hosts)
                        if (tools / "bin" / f"Host{VCVARS_NAMES[host_arch]}" / target / "cl.exe").is_file()
                    ),
                    None,
                )
                if matched_host is None:
                    continue
                rank, host_arch = matched_host
                key = (vcvars.parent, arch)
                previous = compilers.get(key)
                candidate_rank = (_version(cl_version), -rank)
                if previous is None or (previous[0], -previous[1]) < candidate_rank:
                    compilers[key] = (
                        _version(cl_version),
                        rank,
                        Toolchain(
                            vcvars.parent, arch, kind=MSVC, msvc=cl_version,
                            name=display_name or f"Visual Studio {installation.parent.name}",
                            toolset=toolset_version, host=host_arch,
                        ),
                    )
    return [entry[2] for entry in compilers.values()]


def vcvars_argument(arch: str, host: str | None = None) -> str:
    host_name = VCVARS_NAMES[host or host_architecture()]
    target = VCVARS_NAMES[arch]
    return target if host_name == target else f"{host_name}_{target}"


def msvc_environment_command(compiler: Toolchain) -> tuple[Path, str, Path]:
    """Build a safe cmd.exe invocation to capture the environment from vcvarsall.bat."""
    vcvars = _checked_vcvars(compiler)
    argument = vcvars_argument(compiler.arch, compiler.host or None)
    if compiler.toolset:
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", compiler.toolset):
            raise ValueError(f"Invalid MSVC toolset version: {compiler.toolset}")
        argument = f"{argument} -vcvars_ver={compiler.toolset}"
    cmd = _system_cmd()
    setup = f'call "{vcvars}" {argument}'
    # A failed setup runs again with its messages on stderr, so the build log shows why.
    command = f'"{cmd}" /d /s /c "chcp 65001 >nul 2>&1 & {setup} >nul 2>&1 && set || {setup} 1>&2"'
    return cmd, command, vcvars


def parse_msvc_environment(output: str | bytes, vcvars: Path) -> dict[str, str]:
    """Parse `set` output from vcvarsall.bat and confirm cl.exe is available on PATH."""
    if isinstance(output, bytes):
        try:
            text = output.decode("utf-8")
        except UnicodeDecodeError:
            text = output.decode("oem", errors="replace")
    else:
        text = output
    configured = {}
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator and name:
            configured[name] = value
    path = next((value for name, value in configured.items() if name.casefold() == "path"), "")
    if find_program("cl.exe", path) is None:
        raise RuntimeError(f"Could not set up the MSVC environment with {vcvars}.")
    return configured


def msvc_environment(compiler: Toolchain, environment: dict[str, str]) -> dict[str, str]:
    """Return the environment that vcvarsall.bat sets up for the compiler's architecture."""
    cmd, command, vcvars = msvc_environment_command(compiler)
    result = subprocess.run(
        command, executable=str(cmd), capture_output=True, env=environment, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(f"Could not set up the MSVC environment with {vcvars}.")
    return parse_msvc_environment(result.stdout, vcvars)


def find_program(name: str, path: str | None) -> Path | None:
    """
    Find a program in the absolute directories of a PATH value. Unlike shutil.which on
    Windows, this never searches the current directory or relative PATH entries.
    """
    for entry in (path or "").split(os.pathsep):
        directory = Path(entry.strip().strip('"'))
        if directory.is_absolute() and (directory / name).is_file():
            return directory / name
    return None


def find_build_tool(name: str, qt_bin: Path, tool_dirs: Iterable[str], path: str | None) -> Path | None:
    """Find a build tool shipped in the Qt installation's Tools folder, then on PATH."""
    for parent in qt_bin.parents[:3]:
        for relative in tool_dirs:
            candidate = parent / "Tools" / relative / name
            if candidate.is_file():
                return candidate
    return find_program(name, path)


def toolchains_to_data(kits: dict[str, Toolchain], compilers: list[Toolchain]) -> dict:
    def encode(toolchain: Toolchain) -> dict:
        return {**asdict(toolchain), "directory": str(toolchain.directory)}

    return {
        "kits": {label: encode(kit) for label, kit in kits.items()},
        "compilers": [encode(compiler) for compiler in compilers],
    }


def toolchains_from_data(data: dict) -> tuple[dict[str, Toolchain], list[Toolchain]]:
    """Rebuild cached toolchains, dropping entries whose programs no longer exist."""
    if not isinstance(data, dict):
        return {}, []

    def decode(entry, programs) -> Toolchain | None:
        raw_dir = entry.get("directory")
        if not isinstance(raw_dir, str) or not raw_dir or "\x00" in raw_dir:
            return None
        directory = Path(raw_dir)
        if not directory.is_absolute() or directory.drive.startswith("\\\\"):
            return None
        try:
            if _is_reparse_link(directory):
                return None
            resolved = directory.resolve(strict=True)
            if not resolved.is_dir():
                return None
            for name in programs:
                candidate = resolved / name
                if _is_reparse_link(candidate):
                    return None
                program = candidate.resolve(strict=True)
                if program.parent != resolved or not program.is_file():
                    return None
        except (OSError, RuntimeError, ValueError):
            return None

        string_fields = ("arch", "gcc", "version", "kind", "msvc", "name", "toolset", "host")
        if any(field in entry and not isinstance(entry[field], str) for field in string_fields):
            return None
        try:
            toolchain = Toolchain(**{**entry, "directory": resolved})
        except (TypeError, KeyError):
            return None
        if toolchain.kind not in (MINGW, MSVC):
            return None
        if toolchain.arch and toolchain.arch not in VCVARS_NAMES:
            return None
        if toolchain.host and toolchain.host not in VCVARS_NAMES:
            return None
        if toolchain.kind == MSVC and (
            not toolchain.arch
            or (toolchain.toolset and not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", toolchain.toolset))
        ):
            return None
        return toolchain

    kits = {}
    raw_kits = data.get("kits")
    if isinstance(raw_kits, dict):
        for label, entry in raw_kits.items():
            if isinstance(label, str) and isinstance(entry, dict) and (kit := decode(entry, KIT_PROGRAMS)):
                kits[label] = kit
    compilers = []
    raw_compilers = data.get("compilers")
    if isinstance(raw_compilers, list):
        for entry in raw_compilers:
            if isinstance(entry, dict):
                programs = ("vcvarsall.bat",) if entry.get("kind") == MSVC else MINGW_PROGRAMS
                if compiler := decode(entry, programs):
                    compilers.append(compiler)
    return kits, compilers


def _absolute_path(value: str) -> Path | None:
    path = Path(os.path.expanduser(os.path.expandvars(value.strip().strip('"'))))
    return path if path.is_absolute() and not path.drive.startswith("\\\\") else None


def _environment_paths(*names: str) -> list[Path]:
    return [path for name in names if (path := _absolute_path(os.environ.get(name, "")))]


def _common_qt_locations() -> list[Path]:
    locations = []
    for drive in QDir.drives():
        root = drive.absoluteFilePath()
        if win32file.GetDriveType(root) == win32con.DRIVE_FIXED:
            locations.append(Path(root) / "Qt")
    locations.extend(
        path / "Qt"
        for path in _environment_paths("ProgramFiles", "ProgramFiles(x86)", "USERPROFILE", "LOCALAPPDATA")
    )
    locations.extend(path / "Programs" / "Qt" for path in _environment_paths("LOCALAPPDATA"))
    return locations


def _creator_locations(filename: str, key: str) -> list[Path]:
    locations = []
    for profile in _environment_paths("APPDATA"):
        config = profile / "QtProject" / "qtcreator" / filename
        try:
            data = ElementTree.parse(config)
        except FileNotFoundError:
            continue
        except (OSError, ElementTree.ParseError) as exc:
            logger.warning(f"Could not read Qt Creator settings {config}: {exc}")
            continue
        for entry in data.iterfind(f".//value[@key='{key}']"):
            if entry.text and (program := _absolute_path(entry.text)):
                locations.append(program.parent)
    return locations


def _children(directory: Path, *, within: Path | None = None) -> list[Path]:
    try:
        if within is not None and not directory.resolve().is_relative_to(within.resolve()):
            return []
        return sorted(directory.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    except (OSError, RuntimeError) as exc:
        logger.warning(f"Could not scan tools in {directory}: {exc}")
        return []


def _qt_bins(locations: Iterable[Path]) -> Iterable[tuple[Path, Path]]:
    for root in dict.fromkeys(locations):
        yield root, root
        if root.name.casefold() == "bin":
            continue
        yield root, root / "bin"
        versions = [root] if QT_VERSION.match(root.name) else [
            entry for entry in _children(root) if QT_VERSION.match(entry.name)
        ]
        for version in versions:
            for kit in _children(version, within=root):
                yield root, kit / "bin"


def _mingw_bins(locations: Iterable[Path]) -> Iterable[tuple[Path, Path]]:
    for root in dict.fromkeys(locations):
        yield root, root
        if root.name.casefold() == "bin":
            continue
        yield root, root / "bin"
        for compiler in _children(root / "Tools", within=root):
            if compiler.name.casefold().startswith("mingw"):
                yield root, compiler / "bin"


def _read_toolchains(
    candidates: Iterable[tuple[Path, Path]],
    programs: tuple[str, str],
    read: Callable[[Path], Toolchain],
) -> dict[Path, Toolchain]:
    found = {}
    for root, path in candidates:
        try:
            directory = path.resolve(strict=True)
            if (
                directory in found
                or not directory.is_relative_to(root.resolve())
                or not directory.is_dir()
            ):
                continue
            for name in programs:
                program = (directory / name).resolve(strict=True)
                if program.parent != directory or not program.is_file():
                    break
            else:
                found[directory] = read(directory)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(f"Could not inspect toolchain {path}: {exc}")
    return found


def discover_toolchains(folder: str | None = None) -> tuple[dict[str, Toolchain], list[Toolchain]]:
    """Find Windows Qt kits with MinGW and MSVC compilers using directories and installed metadata."""
    common = _common_qt_locations()
    path_dirs = [
        directory for value in os.environ.get("PATH", "").split(os.pathsep)
        if (directory := _absolute_path(value))
    ]
    qt_roots = common + _environment_paths("QTDIR", "QT_ROOT", "QT_DIR", "QT_HOME")
    qt_direct = _creator_locations("qtversion.xml", "QMakePath") + path_dirs
    mingw_roots = common + _environment_paths("MINGW_ROOT", "MINGW_HOME")
    mingw_direct = _creator_locations("toolchains.xml", "ProjectExplorer.GccToolChain.Path") + path_dirs
    if folder:
        root = Path(folder)
        qt_roots, qt_direct = [root], []
        mingw_roots = [root, *root.parents[:3], *mingw_roots]

    qt_bins = _read_toolchains(
        chain(_qt_bins(qt_roots), ((path, path) for path in qt_direct)),
        KIT_PROGRAMS,
        qt_toolchain,
    )
    mingw_roots.extend(root for bin_path in qt_bins for root in bin_path.parents[:3])
    mingw = _read_toolchains(
        chain(_mingw_bins(mingw_roots), ((path, path) for path in mingw_direct)),
        MINGW_PROGRAMS,
        mingw_toolchain,
    )
    kits = {}
    for kit in qt_bins.values():
        label = kit.directory.parent.name
        supported = kit.kind == MSVC or kit.gcc or label.casefold().startswith(MINGW)
        if not kit.version or not supported or label.casefold().startswith("winrt"):
            continue
        name = f"Qt {kit.version} ({label})"
        if name in kits:
            name = f"{name} — {kit.directory.parent}"
        kits[name] = kit
    compilers = list(mingw.values())
    if any(kit.kind == MSVC for kit in kits.values()):
        compilers.extend(msvc_toolchains())
    return kits, compilers
