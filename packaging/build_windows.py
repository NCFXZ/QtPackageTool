# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the Windows x64 standalone application and its per-user EXE installer."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys

from delta import MANIFEST_NAME, build_delta, compile_installer, create_manifest, encode_manifest, file_sha256, load_manifest, snapshot_manifest, version_tuple, write_manifest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
STANDALONE = DIST / "main.dist"


def metadata() -> dict:
    return runpy.run_path(str(ROOT / "src" / "app" / "metadata.py"))


def input_digest() -> str:
    paths = [*ROOT.glob("src/**/*.py"), *ROOT.glob("resource/config/**/*"), *ROOT.glob("resource/images/**/*")]
    paths.extend(ROOT / path for path in (
        "resource/i18n/zh_CN.qm", "LICENSE", "requirements.txt",
        "packaging/build_windows.py", "packaging/nuitka-package.config.yml",
        "packaging/qt6.conf", "packaging/QtPackageTool.iss", "packaging/delta.py",
        "packaging/updater/Program.cs",
    ))
    digest = hashlib.sha256()
    for path in sorted(path for path in paths if path.is_file()):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def find_iscc(requested: str | None) -> Path:
    candidates = [Path(requested)] if requested else []
    if executable := shutil.which("ISCC.exe"):
        candidates.append(Path(executable))
    for parent in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        candidates.extend(Path(parent) / f"Inno Setup {version}" / "ISCC.exe" for version in (7, 6))
    candidates.extend(ROOT.glob("logs/tools/inno*/ISCC.exe"))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("ISCC.exe was not found; install Inno Setup 6/7 or pass --iscc PATH.")


def build_standalone(values: dict, jobs: int) -> None:
    if os.name != "nt" or sys.version_info[:2] != (3, 11) or struct.calcsize("P") != 8:
        raise RuntimeError("Build with 64-bit Python 3.11 on Windows.")
    if importlib.metadata.version("Nuitka") != "4.4":
        raise RuntimeError("Install the pinned project dependencies (Nuitka 4.4).")
    from PyQt6.QtCore import QLibraryInfo
    qt_translation = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_zh_CN.qm"
    if not qt_translation.is_file():
        raise FileNotFoundError("The installed PyQt6 runtime is missing qtbase_zh_CN.qm.")
    version = values["APP_VERSION"] + ".0"
    command = [
        sys.executable, "-B", "-m", "nuitka",
        "--mode=standalone", "--mingw64", "--enable-plugin=pyqt6",
        "--include-qt-plugins=platforms,styles,iconengines,imageformats,tls,networkinformation",
        "--noinclude-qt-plugins=mediaservice,printsupport,platformthemes",
        "--include-module=_ctypes",
        f"--user-package-configuration-file={ROOT / 'packaging' / 'nuitka-package.config.yml'}",
        "--windows-console-mode=disable", "--output-filename=QtPackageTool.exe",
        f"--output-dir={DIST}", f"--report={DIST / 'compilation-report.xml'}",
        f"--windows-icon-from-ico={ROOT / 'resource' / 'images' / 'logo.ico'}",
        f"--company-name=NCFXZ", f"--product-name={values['APP_NAME']}",
        f"--file-version={version}", f"--product-version={version}",
        f"--file-description={values['APP_NAME']}", "--copyright=Copyright (c) 2025 NCFXZ",
        f"--include-data-dir={ROOT / 'resource' / 'config'}=resource/config",
        f"--include-data-dir={ROOT / 'resource' / 'images'}=resource/images",
        f"--include-data-files={ROOT / 'resource' / 'i18n' / 'zh_CN.qm'}=resource/i18n/zh_CN.qm",
        f"--include-data-files={qt_translation}=PyQt6/Qt6/translations/qtbase_zh_CN.qm",
        f"--include-data-files={ROOT / 'packaging' / 'qt6.conf'}=qt6.conf",
        f"--include-data-files={ROOT / 'LICENSE'}=LICENSE",
        "--assume-yes-for-downloads", "--update-check=never", "--lto=no", f"--jobs={jobs}",
        str(ROOT / "src" / "main.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    build_updater(values)
    verify_standalone()


def build_updater(values: dict) -> None:
    """Compile the independent worker with the Windows Framework compiler."""
    version_tuple(values["APP_VERSION"])
    framework = Path(os.environ["SystemRoot"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319"
    compiler = framework / "csc.exe"
    source = ROOT / "packaging" / "updater" / "Program.cs"
    if not compiler.is_file() or not source.is_file():
        raise FileNotFoundError("The Windows .NET Framework compiler and updater source are required.")
    work = DIST / "updater-build"
    work.mkdir(parents=True, exist_ok=True)
    assembly_info = work / "AssemblyInfo.cs"
    version = values["APP_VERSION"] + ".0"
    assembly_info.write_text(
        'using System.Reflection;\nusing System.Runtime.Versioning;\n'
        f'[assembly: AssemblyVersion("{version}")]\n'
        f'[assembly: AssemblyFileVersion("{version}")]\n'
        '[assembly: AssemblyProduct("Qt Package Tool Updater")]\n'
        '[assembly: TargetFramework(".NETFramework,Version=v4.8")]\n', encoding="utf-8")
    output = work / values["UPDATER_NAME"]
    command = [str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
               f"/out:{output}", f"/win32icon:{ROOT / 'resource' / 'images' / 'logo.ico'}"]
    command.extend(f"/reference:{framework / name}" for name in (
        "System.Web.Extensions.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll", "System.Windows.Forms.dll"))
    subprocess.run([*command, str(source), str(assembly_info)], cwd=ROOT, check=True)
    shutil.copy2(output, STANDALONE / values["UPDATER_NAME"])


def verify_standalone() -> None:
    required = [
        "QtPackageTool.exe", "_ctypes.pyd", "LICENSE", "resource/config/qt_dependencies.json",
        "resource/images/logo.ico", "resource/images/logo.png", "resource/i18n/zh_CN.qm",
        "PyQt6/QtNetwork.pyd", "PyQt6/Qt6/plugins/platforms/qwindows.dll",
        "PyQt6/Qt6/plugins/tls/qschannelbackend.dll",
        "PyQt6/Qt6/translations/qtbase_zh_CN.qm", "qt6.conf",
        metadata()["UPDATER_NAME"],
    ]
    for relative in required:
        if not (STANDALONE / relative).is_file():
            raise FileNotFoundError(f"Standalone output is missing {relative}")
    # Validate the actual extension's imports, including the conda-specific ffi.dll.
    sys.path.insert(0, str(ROOT / "src"))
    from deployment.pe import read_pe_imports
    import win32api

    if read_pe_imports(STANDALONE / "QtPackageTool.exe")[0] != 0x8664:
        raise RuntimeError("QtPackageTool.exe is not Windows x64.")
    if read_pe_imports(STANDALONE / metadata()["UPDATER_NAME"])[0] != 0x8664:
        raise RuntimeError("The updater worker is not Windows x64.")
    info = win32api.GetFileVersionInfo(str(STANDALONE / "QtPackageTool.exe"), "\\")
    expected = tuple(map(int, (metadata()["APP_VERSION"] + ".0").split(".")))
    for name in ("FileVersion", "ProductVersion"):
        high, low = info[name + "MS"], info[name + "LS"]
        if (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF) != expected:
            raise RuntimeError(f"{name} does not match the shared release version.")
    architecture, imports = read_pe_imports(STANDALONE / "_ctypes.pyd")
    if architecture != 0x8664:
        raise RuntimeError("_ctypes.pyd is not Windows x64.")
    for name in imports:
        if name.casefold().startswith(("ffi", "libffi")) and not (STANDALONE / name).is_file():
            raise FileNotFoundError(f"Standalone output is missing the _ctypes dependency {name}")


def build_installer(values: dict, iscc: Path) -> Path:
    verify_standalone()
    installer = compile_installer(STANDALONE, values, iscc, DIST)
    from deployment.pe import read_pe_imports
    if read_pe_imports(installer)[0] not in (0x014C, 0x8664):
        raise RuntimeError("The installer is not a supported Windows executable.")
    return installer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--iscc", help="Path to Inno Setup's ISCC.exe")
    parser.add_argument("--no-installer", action="store_true", help="Build only the standalone application")
    parser.add_argument("--installer-only", action="store_true", help="Package an existing standalone build")
    parser.add_argument("--base-manifest", type=Path, help="Previous stable release's exact files manifest for a delta update")
    args = parser.parse_args()
    if args.no_installer and args.installer_only:
        parser.error("--no-installer and --installer-only cannot be used together")
    values = metadata()
    if values["FILE_MANIFEST_NAME"] != MANIFEST_NAME:
        raise RuntimeError("The release manifest filename must match the updater protocol.")
    base_manifest = None
    if args.base_manifest:
        base_manifest = snapshot_manifest(args.base_manifest, DIST / "delta-inputs")
        base = load_manifest(base_manifest)
        if version_tuple(base["version"]) >= version_tuple(values["APP_VERSION"]):
            parser.error("--base-manifest must belong to an older stable release")
    source_digest = input_digest()
    DIST.mkdir(exist_ok=True)
    if not args.installer_only:
        build_standalone(values, max(1, args.jobs))
        write_manifest(STANDALONE, values["APP_VERSION"])
    if source_digest != input_digest():
        raise RuntimeError("Application sources, resources or build inputs changed during the build; rebuild the frozen inputs.")
    build_info = DIST / "standalone-build.json"
    with (STANDALONE / "QtPackageTool.exe").open("rb") as stream:
        executable_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    recorded_build = {"source_sha256": source_digest, "exe_sha256": executable_digest,
                      "updater_sha256": file_sha256(STANDALONE / values["UPDATER_NAME"]),
                      "manifest_sha256": file_sha256(STANDALONE / MANIFEST_NAME)}
    if args.installer_only:
        recorded = json.loads(build_info.read_text(encoding="utf-8"))
        if recorded != recorded_build:
            raise RuntimeError("The existing standalone build does not match the current frozen build inputs.")
        if encode_manifest(create_manifest(STANDALONE, values["APP_VERSION"])) != (STANDALONE / MANIFEST_NAME).read_bytes():
            raise RuntimeError("The existing standalone files differ from the recorded file manifest.")
    else:
        build_info.write_text(json.dumps(recorded_build, indent=2) + "\n", encoding="utf-8")
    if not args.no_installer:
        installer_source_digest = hashlib.sha256((ROOT / "packaging" / "QtPackageTool.iss").read_bytes()).hexdigest()
        iscc = find_iscc(args.iscc)
        installer = build_installer(values, iscc)
        exported_manifest = DIST / MANIFEST_NAME
        shutil.copyfile(STANDALONE / MANIFEST_NAME, exported_manifest)
        assets = [installer, exported_manifest]
        if base_manifest:
            archive = build_delta(base_manifest, exported_manifest, STANDALONE, values, iscc, DIST)
            if archive.stat().st_size < installer.stat().st_size:
                assets.append(archive)
            else:
                print("Delta ZIP is not smaller than the full installer; omitting it from release assets.", flush=True)
        if source_digest != input_digest():
            raise RuntimeError("Build inputs changed while compiling the installer; rebuild before publishing.")
        if installer_source_digest != hashlib.sha256((ROOT / "packaging" / "QtPackageTool.iss").read_bytes()).hexdigest():
            raise RuntimeError("The installer script changed while compiling; rebuild it before publishing.")
        with installer.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {
            "version": values["APP_VERSION"], "installer": installer.name,
            "size": installer.stat().st_size,
            "sha256": digest,
            "source_sha256": source_digest,
            "installer_source_sha256": installer_source_digest,
            "assets": [{"name": path.name, "size": path.stat().st_size, "sha256": file_sha256(path)} for path in assets],
        }
        (DIST / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
