# SPDX-License-Identifier: GPL-3.0-or-later

"""Run real Inno full-install ownership guards with isolated synthetic payloads."""

import json
import os
from pathlib import Path
import pickle
import runpy
import subprocess
import tempfile

import delta
import verify_installer as registry


ROOT = Path(__file__).resolve().parents[1]


def main():
    import win32api
    import win32con
    import win32event
    if registry.registry_snapshot(registry.UNINSTALL) is not None:
        raise RuntimeError("An existing application registration must not be replaced by this fixture.")
    try:
        mutex = win32event.OpenMutex(win32con.SYNCHRONIZE, False, "QtPackageTool.Application")
    except win32api.error as error:
        if error.winerror != 2:
            raise
    else:
        mutex.Close()
        raise RuntimeError("A user application is running; no test was started.")
    work = Path(tempfile.mkdtemp(prefix="full-ownership-", dir=ROOT / "logs"))
    original = registry.registry_snapshot(registry.SETTINGS)
    (work / "settings-before.pickle").write_bytes(pickle.dumps(original))
    values = runpy.run_path(str(ROOT / "src/app/metadata.py"))
    values["APP_NAME"] += " Ownership QA " + work.name[-8:]
    windows = Path(os.environ["SystemRoot"])
    framework = windows / "Microsoft.NET/Framework64/v4.0.30319"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0

    def run(arguments):
        return subprocess.run(list(map(str, arguments)), capture_output=True, timeout=90,
                              startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)

    def require(arguments):
        result = run(arguments)
        if result.returncode:
            raise RuntimeError(f"Fixture command failed: {arguments[0]}: {result.returncode}: {result.stdout!r} {result.stderr!r}")

    base, target = work / "base", work / "target"
    base.mkdir()
    target.mkdir()
    source = work / "Fixture.cs"
    source.write_text("class Fixture { static int Main() { return 0; } }", encoding="ascii")
    require([framework / "csc.exe", "/nologo", "/target:winexe", "/platform:x64", "/out:" + str(base / "QtPackageTool.exe"), source])
    (target / "QtPackageTool.exe").write_bytes((base / "QtPackageTool.exe").read_bytes())
    for bundle, content in ((base, b"old"), (target, b"new")):
        (bundle / "changed.txt").write_bytes(content)
    obsolete_relative = "PyQt6/Qt6/plugins/styles/obsolete.dll"
    obsolete = base / obsolete_relative
    obsolete.parent.mkdir(parents=True)
    obsolete.write_bytes(b"old managed plugin")
    added = target / "new/nested.txt"
    added.parent.mkdir()
    added.write_bytes(b"new managed file")
    delta.write_manifest(base, "1.0.0")
    delta.write_manifest(target, "1.0.1")
    installers = {}
    for version in (6, 7):
        output = work / f"inno{version}"
        compiler = ROOT / f"logs/tools/inno{version}/ISCC.exe"
        installers[version] = tuple(delta.compile_installer(bundle, {**values, "APP_VERSION": release}, compiler, output, output_name=name)
                                    for bundle, release, name in ((base, "1.0.0", "base.exe"), (target, "1.0.1", "target.exe")))
    checks = []
    active_install = None

    def setup(installer, install, name):
        return run([installer, "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/RESTARTAPP=0",
                    "/DIR=" + str(install), "/LOG=" + str(work / (name + ".log"))])

    def uninstall(install):
        path = install / "unins000.exe"
        if path.is_file():
            require([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        assert registry.registry_snapshot(registry.UNINSTALL) is None

    try:
        for version in (6, 7):
            cases = ("upgrade", "reinstall", "new-target-file", "new-target-directory", "parent-file", "bad-manifest", "missing-manifest", "duplicate-manifest", "uninstaller-in-manifest", "junction-parent") if version == 7 else ("upgrade", "new-target-file")
            for case in cases:
                name = f"inno{version}-{case}"
                install = work / name / "安装 目录"
                active_install = install
                assert setup(installers[version][0], install, name + "-base").returncode == 0
                original_manifest = (install / delta.MANIFEST_NAME).read_bytes()
                plugin = install / "PyQt6/Qt6/plugins/styles/user-plugin.dll"
                plugin.write_bytes(b"unknown user plugin")
                user = install / "user.txt"
                user.write_bytes(b"unknown user file")
                junction = None
                if case in ("new-target-file", "new-target-directory"):
                    collision = install / "new/nested.txt"
                    collision.parent.mkdir()
                    if case.endswith("directory"):
                        collision.mkdir()
                        (collision / "user.txt").write_bytes(b"keep")
                    else:
                        collision.write_bytes(b"keep")
                elif case == "parent-file":
                    (install / "new").write_bytes(b"keep parent")
                elif case == "bad-manifest":
                    (install / delta.MANIFEST_NAME).write_bytes(b"not-json")
                elif case == "missing-manifest":
                    (install / delta.MANIFEST_NAME).unlink()
                elif case in ("duplicate-manifest", "uninstaller-in-manifest"):
                    data = delta.load_manifest(install / delta.MANIFEST_NAME)
                    data["files"].append({**data["files"][0], "path": data["files"][0]["path"].upper() if case.startswith("duplicate") else "unins000.exe"})
                    (install / delta.MANIFEST_NAME).write_bytes((json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
                elif case == "junction-parent":
                    external = work / "junction-external"
                    external.mkdir(exist_ok=True)
                    (external / "nested.txt").write_bytes(b"external keep")
                    junction = install / "new"
                    quoted_link = str(junction).replace("'", "''")
                    quoted_target = str(external).replace("'", "''")
                    require([windows / "System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             f"New-Item -ItemType Junction -Path '{quoted_link}' -Value '{quoted_target}' | Out-Null"])
                before = (install / delta.MANIFEST_NAME).read_bytes() if (install / delta.MANIFEST_NAME).exists() else None
                result = setup(installers[version][1], install, name + "-target")
                success = case in ("upgrade", "reinstall")
                assert (result.returncode == 0) == success, (name, result.returncode)
                if success:
                    assert (install / "changed.txt").read_bytes() == b"new"
                    assert (install / "new/nested.txt").read_bytes() == b"new managed file"
                    assert not (install / obsolete_relative).exists()
                    if case == "reinstall":
                        assert setup(installers[version][1], install, name + "-again").returncode == 0
                else:
                    assert (install / "changed.txt").read_bytes() == b"old"
                    assert ((install / delta.MANIFEST_NAME).read_bytes() if (install / delta.MANIFEST_NAME).exists() else None) == before
                    if case == "new-target-file": assert (install / "new/nested.txt").read_bytes() == b"keep"
                    if case == "new-target-directory": assert (install / "new/nested.txt/user.txt").read_bytes() == b"keep"
                    if case == "parent-file": assert (install / "new").read_bytes() == b"keep parent"
                    if junction is not None:
                        assert (work / "junction-external/nested.txt").read_bytes() == b"external keep"
                        os.rmdir(junction)
                    (install / delta.MANIFEST_NAME).write_bytes(original_manifest)
                assert plugin.read_bytes() == b"unknown user plugin" and user.read_bytes() == b"unknown user file"
                uninstall(install)
                assert plugin.exists() and user.exists()
                active_install = None
                checks.append(name)
                print("PASS:", name, flush=True)
    finally:
        cleanup_error = None
        try:
            if active_install is not None:
                uninstall(active_install)
        except Exception as error:
            cleanup_error = str(error)
        finally:
            registry.restore_registry(registry.SETTINGS, original)
            assert registry.registry_snapshot(registry.SETTINGS) == original
            summary = {"checks": checks, "user_settings_restored": True,
                       "test_registration_removed": registry.registry_snapshot(registry.UNINSTALL) is None,
                       "cleanup_error": cleanup_error}
            (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print("Ownership verification logs:", work, flush=True)
        if cleanup_error:
            raise RuntimeError(cleanup_error)


if __name__ == "__main__":
    main()
