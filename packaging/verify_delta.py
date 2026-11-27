# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise the real copied updater and Inno installers with isolated synthetic releases."""

import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import warnings
import winreg
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import delta

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6A810F1B-57CE-45D3-9679-1DD9951F3069}_is1"
SETTINGS = r"Software\QtPackageTool\QtPackageTool"
MANIFEST = "QtPackageTool-files.json"
INSTALLER = "QtPackageTool-Setup-x64.exe"
HELPER = "QtPackageTool.Updater.exe"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot(path: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return None
    with key:
        return {
            "values": [winreg.EnumValue(key, index) for index in range(winreg.QueryInfoKey(key)[1])],
            "children": {
                name: snapshot(path + "\\" + name)
                for name in (winreg.EnumKey(key, index) for index in range(winreg.QueryInfoKey(key)[0]))
            },
        }


def restore(path: str, saved) -> None:
    current = snapshot(path)
    if current is not None:
        for name in current["children"]:
            restore(path + "\\" + name, None)
        winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, path, winreg.KEY_WOW64_64KEY)
    if saved is not None:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
            for name, value, kind in saved["values"]:
                winreg.SetValueEx(key, name, 0, kind, value)
        for name, child in saved["children"].items():
            restore(path + "\\" + name, child)


def close_own_failure_dialog(process_id: int) -> None:
    """Dismiss only this test worker's error dialog, never a user's application."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    user32.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def visit(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id:
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 256)
            if title.value == "Qt Package Tool Update":
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE on our fixture worker, independent of localized button IDs.
        return 1

    user32.EnumWindows(visit, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    windows = Path(os.environ["SystemRoot"])
    csc = windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if os.name != "nt" or not csc.is_file():
        raise RuntimeError("This integration requires Windows x64 and its .NET Framework compiler.")
    # Refuse to test around a real running application; no process is forcefully terminated.
    import win32event
    import win32con
    import win32api
    try:
        handle = win32event.OpenMutex(win32con.SYNCHRONIZE, False, "QtPackageTool.Application")
    except win32api.error as exc:
        if exc.winerror != 2:
            raise
    else:
        handle.Close()
        raise RuntimeError("Close Qt Package Tool instances before delta installer verification.")
    build = runpy.run_path(str(ROOT / "packaging/build_windows.py"))
    iscc = args.iscc.resolve(strict=True) if args.iscc else build["find_iscc"](None)
    work = Path(tempfile.mkdtemp(prefix="delta-verification-", dir=ROOT / "logs"))
    app_parent = Path(tempfile.mkdtemp(prefix="QtPackageTool-delta-fixtures-"))
    values = build["metadata"]()
    values["APP_NAME"] = "QtPackageTool Delta Verification " + uuid.uuid4().hex[:8]
    settings_existed = snapshot(r"Software\QtPackageTool") is not None
    originals = {UNINSTALL: snapshot(UNINSTALL), SETTINGS: snapshot(SETTINGS)}
    (work / "user-registry-before.json").write_text(
        json.dumps(originals, ensure_ascii=False, indent=2, default=lambda value: {"bytes_base64": base64.b64encode(value).decode("ascii")}),
        encoding="utf-8",
    )
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    checks = []
    active_install = None

    def run(command, *, timeout=180):
        completed = subprocess.run(
            list(map(str, command)), cwd=ROOT, env=env, startupinfo=startup,
            creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=timeout,
        )
        if completed.returncode:
            raise RuntimeError(f"Fixture command failed ({completed.returncode}): {command[0]}\n{completed.stdout.decode(errors='replace')}\n{completed.stderr.decode(errors='replace')}")
        return completed

    def compile_cs(source: str, destination: Path, *, helper=False):
        destination.parent.mkdir(parents=True, exist_ok=True)
        code = destination.with_suffix(".cs")
        if helper:
            code = ROOT / "packaging/updater/Program.cs"
        else:
            code.write_text(source, encoding="utf-8")
        references = ["System.Web.Extensions.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll", "System.Windows.Forms.dll"] if helper else []
        run([csc, "/nologo", "/target:winexe", "/platform:x64", f"/out:{destination}", *[f"/reference:{item}" for item in references], code])

    def confirm(message: str):
        checks.append(message)
        print("PASS:", message, flush=True)
        (work / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")

    def uninstall(install: Path):
        uninstaller = install / "unins000.exe"
        if uninstaller.is_file():
            run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={work / ('uninstall-' + install.parent.name + '.log')}"])

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SETTINGS, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, "deltaVerification", 0, winreg.REG_SZ, "preserve-user-settings")
        tools = work / "tools"
        helper = tools / HELPER
        compile_cs("", helper, helper=True)
        base = work / "base"
        target = work / "target"
        for bundle, version in ((base, "1.0.0"), (target, "1.0.1")):
            bundle.mkdir()
            compile_cs('''using System; using System.IO; using System.Threading;
internal static class Fixture {
 static int Main(string[] args) {
  using (Mutex running = new Mutex(false, "QtPackageTool.Application")) {
   if (args.Length > 0 && args[0] == "--hold") { Thread.Sleep(1600); return 0; }
   File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"fixture-started.txt"), "VERSION");
   return 0;
  }
 }
}'''.replace("VERSION", version), bundle / "QtPackageTool.exe")
            shutil.copyfile(helper, bundle / HELPER)
            (bundle / "changed.txt").write_text("changed in " + version, encoding="ascii")
            plugin = bundle / "PyQt6/Qt6/plugins/styles/unchanged.dll"
            plugin.parent.mkdir(parents=True)
            plugin.write_bytes(b"unchanged plugin must survive a delta")
        shared = os.urandom(2 * 1024 * 1024)
        (base / "common.bin").write_bytes(shared)
        (target / "common.bin").write_bytes(shared)
        (base / "removed.txt").write_text("removed in target", encoding="ascii")
        (target / "added.txt").write_text("added in target", encoding="ascii")
        base_manifest = delta.write_manifest(base, "1.0.0")
        target_manifest = delta.write_manifest(target, "1.0.1")
        output = work / "installers"
        output.mkdir()
        base_values = {**values, "APP_VERSION": "1.0.0"}
        target_values = {**values, "APP_VERSION": "1.0.1"}
        base_setup = delta.compile_installer(base, base_values, iscc, output, output_name="Fixture-Base.exe")
        target_setup = delta.compile_installer(target, target_values, iscc, output, output_name="Fixture-Target.exe")
        delta_zip = delta.build_delta(base_manifest, target_manifest, target, target_values, iscc, output)
        target_bytes = target_manifest.read_bytes()
        with zipfile.ZipFile(delta_zip) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}

        # These installers intentionally mutate known fixture files then fail. They expose no product test switch.
        mutation = '''using System; using System.IO; using System.Threading; using Microsoft.Win32;
internal static class FailureFixture {
 static int Main(string[] args) {
  string folder = null;
  foreach(string arg in args) if(arg.StartsWith("/DIR=",StringComparison.OrdinalIgnoreCase)) folder=arg.Substring(5);
  if(folder==null) return 99;
  File.WriteAllText(Path.Combine(folder,"QtPackageTool.exe"),"partial executable");
  File.Delete(Path.Combine(folder,"removed.txt"));
  File.WriteAllText(Path.Combine(folder,"added.txt"),"partial target file");
  using(RegistryKey user=RegistryKey.OpenBaseKey(RegistryHive.CurrentUser,RegistryView.Registry64))
  using(RegistryKey key=user.CreateSubKey(@"REGKEY")) key.SetValue("DisplayVersion","999.0.0");
  try { File.WriteAllText(Path.Combine(folder,"changed.txt"),"partial changed file"); } catch(IOException) { }
  Thread.Sleep(DELAY);
  return 9;
 }
}'''.replace("REGKEY", UNINSTALL)
        bad_delta = tools / "partial-delta.exe"
        bad_full = tools / "partial-full.exe"
        slow_bad_full = tools / "slow-partial-full.exe"
        compile_cs(mutation.replace("DELAY", "0"), bad_delta)
        compile_cs(mutation.replace("DELAY", "0"), bad_full)
        compile_cs(mutation.replace("DELAY", "4500"), slow_bad_full)
        locker = tools / "locker.exe"
        compile_cs('''using System; using System.IO; using System.Threading;
internal static class Locker { static int Main(string[] args) {
 using(FileStream stream=new FileStream(args[0],FileMode.Open,FileAccess.Read,FileShare.Read)) {
  File.WriteAllText(args[1],"ready"); Thread.Sleep(3500);
 } return 0;
} }''', locker)

        def make_zip(destination: Path, altered_entries):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Duplicate name")
                with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, data in altered_entries:
                        archive.writestr(name, data)

        def execute_case(name: str, *, edit=None, cache_full=None, expected_full=False, fail=False, lock=False, wait=False, wrong_hash=False):
            nonlocal active_install
            case = work / name
            cache = case / "cache" / "download"
            cache.mkdir(parents=True)
            install = app_parent / name / "安装 目录"
            install.parent.mkdir(parents=True)
            run([base_setup, "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/RESTARTAPP=0", f"/DIR={install}", f"/LOG={case / 'base-install.log'}"])
            active_install = install
            baseline_registration = snapshot(UNINSTALL)
            baseline_uninstall = {path.name: sha(path) for path in install.glob("unins*") if path.is_file()}
            user_file = install / "user-file.txt"
            user_file.write_text("preserve unknown user file", encoding="ascii")
            extra = install / "user-folder/also-preserve.txt"
            extra.parent.mkdir()
            extra.write_text("user folder", encoding="ascii")
            before = {file["path"]: file["sha256"] for file in delta.load_manifest(base_manifest)["files"]}
            archive = cache / delta_zip.name
            if edit is None:
                shutil.copyfile(delta_zip, archive)
            else:
                edit(archive)
            shutil.copyfile(helper, cache / HELPER)
            selected_full = cache_full or target_setup
            shutil.copyfile(selected_full, cache / INSTALLER)
            shutil.copyfile(target_manifest, cache / MANIFEST)
            waiting = None
            locking = None
            wait_pid = 2147483646
            if wait:
                waiting = subprocess.Popen([str(install / "QtPackageTool.exe"), "--hold"], env=env, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
                wait_pid = waiting.pid
                time.sleep(0.2)
            if lock:
                ready = cache / "lock-ready.txt"
                locking = subprocess.Popen([str(locker), str(install / "changed.txt"), str(ready)], env=env, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert ready.exists(), "The fixture file lock was not established"
            plan = {
                "schema": 1, "app_dir": str(install), "wait_pid": wait_pid,
                "base_version": "1.0.0", "target_version": "1.0.1",
                "delta": {"path": str(archive), "size": archive.stat().st_size, "sha256": "0" * 64 if wrong_hash else sha(archive)},
                "full": {"url": "https://github.com/NCFXZ/QtPackageTool/releases/download/v1.0.1/" + INSTALLER,
                         "size": selected_full.stat().st_size, "sha256": sha(selected_full)},
                "manifest": {"url": "https://github.com/NCFXZ/QtPackageTool/releases/download/v1.0.1/" + MANIFEST,
                             "size": len(target_bytes), "sha256": sha(target_manifest)},
            }
            plan_path = cache / "plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            began = time.monotonic()
            process = subprocess.Popen([str(cache / HELPER), "--plan", str(plan_path)], env=env, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 240
            result_path = cache / "helper-result.json"
            while process.poll() is None and time.monotonic() < deadline:
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if not result["success"]:
                        close_own_failure_dialog(process.pid)
                time.sleep(0.1)
            if process.poll() is None:
                raise RuntimeError("The fixture worker did not finish; it was not forcefully terminated. See " + str(cache))
            if waiting is not None:
                assert waiting.wait(timeout=10) == 0
                assert time.monotonic() - began >= 1.0, "The worker did not wait for normal application exit"
            if locking is not None:
                assert locking.wait(timeout=10) == 0
            result = json.loads(result_path.read_text(encoding="utf-8"))
            log = (cache / "helper.log").read_text(encoding="utf-8")
            if fail:
                assert process.returncode != 0 and not result["success"], result
                for relative, checksum in before.items():
                    assert sha(install / relative) == checksum, relative
                assert not (install / "added.txt").exists()
                assert snapshot(UNINSTALL) == baseline_registration
                assert {path.name: sha(path) for path in install.glob("unins*") if path.is_file()} == baseline_uninstall
                assert Path(result["backup_dir"]).is_dir(), result
                assert "rollback_complete" in log
            else:
                assert process.returncode == 0 and result["success"], result
                assert result["mode"] == ("full" if expected_full else "delta"), result
                for file in delta.load_manifest(target_manifest)["files"]:
                    assert sha(install / file["path"]) == file["sha256"], file["path"]
                assert not (install / "removed.txt").exists()
                deadline = time.monotonic() + 10
                while not (install / "fixture-started.txt").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert (install / "fixture-started.txt").read_text() == "1.0.1"
            assert user_file.read_text() == "preserve unknown user file"
            assert extra.read_text() == "user folder"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                assert winreg.QueryValueEx(key, "deltaVerification")[0] == "preserve-user-settings"
            if expected_full or fail:
                assert log.count(" | full_fallback | ") == 1, log
                assert log.count(" | full_installer_starting | ") == 1, log
                assert "full_cache_verified" in log
                marker = json.loads((cache.parent / "full-only.json").read_text(encoding="utf-8"))
                assert marker == {"schema": 1, "base_version": "1.0.0", "target_version": "1.0.1", "full_sha256": sha(selected_full)}
            else:
                assert "full_fallback" not in log
                assert not (cache.parent / "full-only.json").exists()
            uninstall(install)
            active_install = None
            assert not (install / "QtPackageTool.exe").exists()
            assert not (install / "added.txt").exists(), "The same-AppId delta did not append new files to the uninstall log"
            assert user_file.exists() and extra.exists()
            confirm(name)

        execute_case("delta-changed-added-removed-and-uninstall", wait=True)

        def mismatch(destination):
            metadata = json.loads(entries["delta.json"])
            metadata["base_manifest_sha256"] = "0" * 64
            make_zip(destination, [(name, json.dumps(metadata).encode() if name == "delta.json" else data) for name, data in entries.items()])

        execute_case("baseline-mismatch-full-fallback", edit=mismatch, expected_full=True)
        execute_case("zip-sha-mismatch-full-fallback", wrong_hash=True, expected_full=True)
        execute_case("zip-path-rejection-full-fallback", edit=lambda path: make_zip(path, [("../escape.exe" if name == "installer.exe" else name, data) for name, data in entries.items()]), expected_full=True)
        execute_case("zip-duplicate-rejection-full-fallback", edit=lambda path: make_zip(path, [("delta.json" if name == "installer.exe" else name, data) for name, data in entries.items()]), expected_full=True)
        execute_case("zip-metadata-size-rejection-full-fallback", edit=lambda path: make_zip(path, [(name, b"x" * (1024 * 1024 + 1) if name == "delta.json" else data) for name, data in entries.items()]), expected_full=True)

        def oversized_installer(destination):
            image = bytearray(delta_zip.read_bytes())
            offset = image.find(b"PK\x01\x02")
            while offset >= 0:
                name_length, extra_length, comment_length = struct.unpack_from("<HHH", image, offset + 28)
                if image[offset + 46:offset + 46 + name_length] == b"installer.exe":
                    struct.pack_into("<I", image, offset + 24, 2 * 1024 * 1024 * 1024 + 1)
                    break
                offset = image.find(b"PK\x01\x02", offset + 46 + name_length + extra_length + comment_length)
            else:
                raise AssertionError("Fixture ZIP has no installer central-directory entry")
            destination.write_bytes(image)

        execute_case("zip-installer-size-rejection-full-fallback", edit=oversized_installer, expected_full=True)
        partial_delta = lambda path: make_zip(path, [(name, bad_delta.read_bytes() if name == "installer.exe" else data) for name, data in entries.items()])
        execute_case("partial-delta-and-full-fail-restore-baseline", edit=partial_delta, cache_full=bad_full, fail=True)
        execute_case("early-bad-zip-full-partial-fail-known-target-cleanup", wrong_hash=True, cache_full=bad_full, fail=True)
        execute_case("locked-file-rollback-and-single-full-failure", edit=partial_delta, cache_full=slow_bad_full, fail=True, lock=True)
        confirm("all synthetic delta/full fixtures preserved user files and QSettings")
    finally:
        cleanup_error = None
        try:
            if active_install is not None:
                uninstall(active_install)
        except Exception as error:
            cleanup_error = str(error)
        finally:
            for path, saved in originals.items():
                restore(path, saved)
                assert snapshot(path) == saved, path
            if not settings_existed and snapshot(r"Software\QtPackageTool") == {"values": [], "children": {}}:
                winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, r"Software\QtPackageTool", winreg.KEY_WOW64_64KEY)
            summary = {"checks": checks, "user_registration_restored": True, "user_settings_restored": True,
                       "fixture_app_root": str(app_parent), "cleanup_error": cleanup_error}
            (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print("User state restored; delta verification logs:", work, flush=True)
        if cleanup_error is not None:
            raise RuntimeError("Fixture cleanup failed after restoring user state: " + cleanup_error)


if __name__ == "__main__":
    main()
