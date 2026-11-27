# SPDX-License-Identifier: GPL-3.0-or-later

"""Verify isolated install, PID waiting, overwrite upgrade and uninstall without changing user preferences."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import winreg

import win32api
import win32con
import win32event
import win32process

from delta import MANIFEST_NAME, encode_manifest, file_sha256, load_manifest

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = r"Software\QtPackageTool\QtPackageTool"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6A810F1B-57CE-45D3-9679-1DD9951F3069}_is1"


def registry_snapshot(path: str):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        return None
    with key:
        children, values, _ = winreg.QueryInfoKey(key)
        return {
            "values": [winreg.EnumValue(key, index) for index in range(values)],
            "children": {name: registry_snapshot(path + "\\" + name) for name in (winreg.EnumKey(key, index) for index in range(children))},
        }


def remove_registry_tree(path: str) -> None:
    snapshot = registry_snapshot(path)
    if snapshot is not None:
        for child in snapshot["children"]:
            remove_registry_tree(path + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


def restore_registry(path: str, snapshot) -> None:
    remove_registry_tree(path)
    if snapshot is not None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name, value, kind in snapshot["values"]:
                winreg.SetValueEx(key, name, 0, kind, value)
        for name, child in snapshot["children"].items():
            restore_registry(path + "\\" + name, child)


def application_pids(executable: Path) -> list[int]:
    matches = []
    for pid in win32process.EnumProcesses():
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
            try:
                path = Path(win32process.GetModuleFileNameEx(handle, 0))
                if path == executable:
                    matches.append(pid)
            finally:
                handle.Close()
        except (OSError, win32api.error):
            continue
    return matches


def stop_test_application(executable: Path) -> None:
    # These processes are idle test launches under a unique logs directory, never user builds.
    for pid in application_pids(executable):
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_INFORMATION |
                win32con.PROCESS_VM_READ | win32con.SYNCHRONIZE, False, pid,
            )
            try:
                # Recheck the path on the handle being terminated, avoiding a reused-PID race.
                if Path(win32process.GetModuleFileNameEx(handle, 0)) == executable:
                    win32api.TerminateProcess(handle, 0)
                    assert win32event.WaitForSingleObject(handle, 10000) == win32con.WAIT_OBJECT_0
            finally:
                handle.Close()
        except win32api.error as exc:
            if exc.winerror not in (87, 1168):  # The test process may already have exited.
                raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    args = parser.parse_args()
    installer = args.installer.resolve(strict=True)
    expected_manifest = (installer.parent / MANIFEST_NAME).read_bytes()
    if registry_snapshot(UNINSTALL) is not None:
        raise RuntimeError("This AppId is already installed for the user; isolated verification would replace its registration.")
    try:
        running = win32event.OpenMutex(win32con.SYNCHRONIZE, False, "QtPackageTool.Application")
    except win32api.error as exc:
        if exc.winerror != 2:
            raise
    else:
        running.Close()
        raise RuntimeError("Close existing Qt Package Tool instances before isolated installation verification.")
    settings_parent_existed = registry_snapshot(r"Software\QtPackageTool") is not None
    original = registry_snapshot(SETTINGS)
    work = Path(tempfile.mkdtemp(prefix="installer-verification-", dir=ROOT / "logs"))
    install = work / "安装 目录"
    executable = install / "QtPackageTool.exe"
    local_data = work / "localdata"
    marker_log = local_data / "QtPackageTool" / "logs" / "preserve.log"
    marker_log.parent.mkdir(parents=True)
    marker_log.write_text("preserve logs across update and uninstall\n", encoding="utf-8")
    env = os.environ.copy()
    windows = Path(os.environ["SystemRoot"])
    env["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    env["LOCALAPPDATA"] = str(local_data)
    env["QT_QPA_PLATFORM"] = "offscreen"
    for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
        env.pop(name, None)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = win32con.SW_HIDE
    checks = []
    if original is not None:
        backup = work / "settings-before.reg"
        subprocess.run(
            [str(windows / "System32" / "reg.exe"), "export", "HKCU\\" + SETTINGS, str(backup), "/y"],
            check=True, capture_output=True, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        backup = work / "settings-before-absent.json"
        backup.write_text(json.dumps({"key": SETTINGS, "parent_existed": settings_parent_existed}) + "\n", encoding="utf-8")
    print("User preference backup:", backup, flush=True)

    def start(command):
        return subprocess.Popen(command, env=env, startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)

    def confirm(message: str) -> None:
        checks.append(message)
        print("PASS:", message, flush=True)

    def run_setup(*extra: str):
        return start([str(installer), "/SP-", "/NORESTART", "/SUPPRESSMSGBOXES", "/NOICONS", f"/DIR={install}", *extra])

    def verify_managed_files():
        manifest_path = install / MANIFEST_NAME
        assert manifest_path.read_bytes() == expected_manifest, "Installed baseline differs from the published manifest"
        manifest = load_manifest(manifest_path)
        assert any(item["path"] == "QtPackageTool.Updater.exe" for item in manifest["files"])
        for item in manifest["files"]:
            path = install / item["path"]
            assert path.stat().st_size == item["size"] and file_sha256(path) == item["sha256"], item["path"]

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SETTINGS) as key:
            winreg.SetValueEx(key, "packagingVerification", 0, winreg.REG_SZ, "preserve-settings")
        initial = run_setup("/VERYSILENT", "/RESTARTAPP=0", f"/LOG={work / 'install.log'}")
        assert initial.wait(timeout=180) == 0
        assert executable.is_file() and (install / "unins000.exe").is_file()
        assert registry_snapshot(UNINSTALL) is not None
        verify_managed_files()
        installed_digest = hashlib.file_digest(executable.open("rb"), "sha256").hexdigest()
        confirm("per-user isolated installation, uninstaller registration and exact managed-file/helper baseline")

        process = start([str(executable)])
        time.sleep(8)
        assert process.poll() is None, f"Standalone application exited with {process.returncode}"
        mutex = win32event.OpenMutex(win32con.SYNCHRONIZE, False, "QtPackageTool.Application")
        mutex.Close()
        assert (local_data / "QtPackageTool" / "logs" / "app.log").is_file()
        log_text = (local_data / "QtPackageTool" / "logs" / "app.log").read_text(encoding="utf-8")
        assert "Unhandled exception" not in log_text
        process.terminate()
        process.wait(timeout=15)
        confirm("installed executable starts with isolated logs, offscreen Qt and system-only PATH")

        obsolete = install / "PyQt6" / "Qt6" / "plugins" / "styles" / "obsolete.dll"
        obsolete.parent.mkdir(parents=True, exist_ok=True)
        obsolete.write_bytes(b"obsolete plugin from an older version")
        previous = load_manifest(install / MANIFEST_NAME)
        previous["files"].append({"path": obsolete.relative_to(install).as_posix(), "size": obsolete.stat().st_size,
                                  "sha256": file_sha256(obsolete)})
        (install / MANIFEST_NAME).write_bytes(encode_manifest(previous))
        user_plugin = obsolete.with_name("user-plugin.dll")
        user_plugin.write_bytes(b"unknown user plugin must be preserved")
        user_file = install / "user-file.txt"
        user_file.write_text("preserve user file", encoding="ascii")
        executable.write_bytes(b"previous version placeholder")
        dummy = subprocess.Popen(
            [sys.executable, "-c", "import time,win32event; mutex=win32event.CreateMutex(None,False,'QtPackageTool.Application'); time.sleep(4)"],
            startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(0.3)
        began = time.monotonic()
        upgrade = run_setup("/SILENT", "/RESTARTAPP=1", f"/WAITPID={dummy.pid}", f"/LOG={work / 'upgrade.log'}")
        time.sleep(1)
        assert dummy.poll() is None and upgrade.poll() is None
        assert executable.read_bytes() == b"previous version placeholder"
        assert dummy.wait(timeout=15) == 0
        assert upgrade.wait(timeout=180) == 0
        assert time.monotonic() - began >= 3
        assert hashlib.file_digest(executable.open("rb"), "sha256").hexdigest() == installed_digest
        assert not obsolete.exists() and user_file.read_text() == "preserve user file"
        assert user_plugin.read_bytes() == b"unknown user plugin must be preserved"
        verify_managed_files()
        confirm("/WAITPID waits for normal exit; upgrade removes only manifest-owned obsolete plugins and preserves unknown user plugins/files")

        deadline = time.monotonic() + 20
        while not application_pids(executable) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert application_pids(executable), "/SILENT /RESTARTAPP=1 did not restart the installed executable"
        time.sleep(4)
        assert application_pids(executable), "Restarted application exited during startup"
        stop_test_application(executable)
        time.sleep(0.5)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS) as key:
            assert winreg.QueryValueEx(key, "packagingVerification")[0] == "preserve-settings"
        assert marker_log.read_text().startswith("preserve logs")
        confirm("/SILENT /RESTARTAPP=1 launches the new application; QSettings and logs survive upgrade")

        uninstall = start([str(install / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={work / 'uninstall.log'}"])
        assert uninstall.wait(timeout=180) == 0
        assert not executable.exists() and registry_snapshot(UNINSTALL) is None
        assert marker_log.exists() and user_file.exists()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS) as key:
            assert winreg.QueryValueEx(key, "packagingVerification")[0] == "preserve-settings"
        confirm("uninstall removes the application and registration while preserving settings, logs and user files")
    finally:
        cleanup_error = None
        try:
            stop_test_application(executable)
            uninstaller = install / "unins000.exe"
            if registry_snapshot(UNINSTALL) is not None and uninstaller.is_file():
                cleanup = start([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={work / 'cleanup-uninstall.log'}"])
                if cleanup.wait(timeout=180) != 0:
                    raise RuntimeError("The test installation could not be uninstalled.")
        except Exception as exc:
            cleanup_error = exc
        finally:
            # Preference restoration must also run when process/uninstaller cleanup fails.
            restore_registry(SETTINGS, original)
            assert registry_snapshot(SETTINGS) == original
            if not settings_parent_existed:
                parent = registry_snapshot(r"Software\QtPackageTool")
                if parent == {"values": [], "children": {}}:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\QtPackageTool")
            summary = {"checks": checks, "user_settings_restored": True, "cleanup_error": str(cleanup_error) if cleanup_error else None}
            (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
            print("User settings restored; verification logs:", work, flush=True)
        if cleanup_error is not None:
            raise RuntimeError("Verification cleanup failed after restoring user preferences.") from cleanup_error


if __name__ == "__main__":
    main()
