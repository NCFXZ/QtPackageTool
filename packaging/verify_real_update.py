# SPDX-License-Identifier: GPL-3.0-or-later

"""Local acceptance checks for the real updater; never publish a QA release."""

import argparse
import base64
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "QtPackageTool-files.json"
REFERENCES = ("System.Web.Extensions.dll", "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll", "System.Windows.Forms.dll")
INSTALLER = "QtPackageTool-Setup-x64.exe"
HELPER = "QtPackageTool.Updater.exe"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_manifest(path: Path, version: str, files: dict[str, bytes]) -> None:
    path.write_text(json.dumps({"schema": 1, "version": version, "files": [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]}, sort_keys=True) + "\n", encoding="utf-8")


def safety_checks(work: Path) -> None:
    """Invoke production preflight without running an installer or touching the user's registry."""
    source = ROOT / "packaging/updater/Program.cs"
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    driver = work / "SafetyProbe.cs"
    driver.write_text(r'''
using System; using System.IO; using System.Reflection; using System.Collections.Generic;
internal static class SafetyProbe {
 static readonly Type P = typeof(Program);
 static readonly BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;
 static object Call(string name, params object[] args) { return P.GetMethod(name,Flags).Invoke(null,args); }
 static void Set(string name, object value) { P.GetField(name,Flags).SetValue(null,value); }
 static bool Rejected(string name, object[] args) {
  try { Call(name,args); return false; }
  catch(TargetInvocationException ex) { Console.WriteLine(ex.InnerException.Message); return true; }
 }
 static int Main(string[] args) {
  if(args[0]=="paths") {
   foreach(string path in new[]{"CON .txt","AUX .json","CONIN$","conout$.dat","a/COM¹ .dat","LPT².log"})
    if(!Rejected("Relative",new object[]{path})) throw new Exception("Unsafe path accepted: "+path);
   if(!Rejected("Absolute",new object[]{"C:relative"})) throw new Exception("Drive-relative path accepted");
   if(!Rejected("VersionParts",new object[]{"1.0.1\n"})) throw new Exception("Version suffix accepted");
   if(!Rejected("HashValue",new object[]{new Dictionary<string,object>{{"sha256",new string('0',64)+"\n"}},"sha256"}))
    throw new Exception("Hash suffix accepted");
   return 0;
  }
  string app=Path.GetFullPath(args[0]);
  Set("AppDir",app); Set("WorkDir",Path.GetDirectoryName(args[1])); Set("BaseVersion","1.0.0");
  object target;
  if(args.Length>3 && args[3]=="cached") {
   Set("TargetVersion","1.0.1"); Set("FileManifestHash",Call("Digest",File.ReadAllBytes(args[1])));
   Set("FileManifestSize",new FileInfo(args[1]).Length);
   target=Call("ObtainTargetManifest");
  } else target=Call("ReadManifest",args[1]);
  var paths=(HashSet<string>)P.GetField("TargetPaths",Flags).GetValue(null);
  bool rejected=false;
  try { Call("PreflightTarget",target); }
  catch(TargetInvocationException ex) {
   if(ex.InnerException.GetType().Name!="UserFileConflictException") throw;
   Console.WriteLine(ex.InnerException.Message); rejected=true;
  }
  bool expected=args[2]=="reject";
  if(rejected!=expected) throw new Exception("Unexpected preflight result");
  if(rejected && paths.Count!=0) throw new Exception("Rejected user paths became rollback targets");
  return 0;
 }
}''', encoding="utf-8")
    probe = work / "SafetyProbe.exe"
    command = [str(compiler), "/nologo", "/target:exe", "/platform:x64", "/main:SafetyProbe", f"/out:{probe}",
               *[f"/reference:{name}" for name in REFERENCES], str(source), str(driver)]
    subprocess.run(command, check=True, cwd=ROOT, capture_output=True)
    checks = []

    def execute(name: str, command: list[str]) -> None:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        (work / (name + ".txt")).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"Safety check {name} failed: " + (result.stdout + result.stderr).decode(errors="replace"))
        checks.append(name)
        print("PASS:", name, flush=True)

    execute("strict-windows-paths-and-fields", [str(probe), "paths"])
    base = {"QtPackageTool.exe": b"baseline managed executable"}
    for name, target, conflict, reject in (
        ("unknown-file", "new.bin", "file", True),
        ("unknown-directory", "new.bin", "directory", True),
        ("parent-is-user-file", "data/new.bin", "parent-file", True),
        ("unrelated-user-file", "data/new.bin", "unrelated", False),
        ("managed-repair", "QtPackageTool.exe", "managed", False),
    ):
        case = work / name
        app = case / "app"
        app.mkdir(parents=True)
        (app / "QtPackageTool.exe").write_bytes(base["QtPackageTool.exe"])
        write_manifest(app / MANIFEST, "1.0.0", base)
        if conflict == "file":
            (app / target).write_bytes(b"unknown user data")
        elif conflict == "directory":
            (app / target).mkdir()
            (app / target / "user.txt").write_bytes(b"unknown nested user data")
        elif conflict == "parent-file":
            (app / "data").write_bytes(b"unknown user file occupying parent")
        elif conflict == "unrelated":
            (app / "data").mkdir()
            (app / "data/user.txt").write_bytes(b"unknown unrelated user data")
        before = {p.relative_to(app).as_posix(): sha(p) for p in app.rglob("*") if p.is_file()}
        target_manifest = case / MANIFEST
        write_manifest(target_manifest, "1.0.1", {**base, target: b"new managed data"})
        execute(name, [str(probe), str(app), str(target_manifest), "reject" if reject else "allow"])
        if reject:
            execute(name + "-early-zip-fallback", [str(probe), str(app), str(target_manifest), "reject", "cached"])
        after = {p.relative_to(app).as_posix(): sha(p) for p in app.rglob("*") if p.is_file()}
        assert after == before, "Preflight changed user or managed data"
    summary = {"checks": checks, "helper_source_sha256": sha(source), "installer_executed": False, "registry_mutated": False}
    (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def installed_checks(work: Path, base_setup: Path, base_manifest: Path, target_bundle: Path, iscc: Path) -> None:
    """Use actual Qt binaries; fault injectors replace only selected test installer payloads."""
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    import win32api
    import win32con
    import win32event
    import win32file
    import win32gui
    import win32process
    from win32com.shell import shell, shellcon
    from PIL import ImageGrab
    from PyQt6.QtCore import QCoreApplication, QSettings

    sys.path.insert(0, str(ROOT / "packaging"))
    import delta
    from verify_delta import UNINSTALL, SETTINGS, close_own_failure_dialog, restore, snapshot
    sys.path.insert(0, str(ROOT / "src"))
    from app.installer import launch_delta_update
    from app.updates.models import Asset, Release

    try:
        running = win32event.OpenMutex(win32con.SYNCHRONIZE, False, "QtPackageTool.Application")
    except win32api.error as error:
        if error.winerror != 2:
            raise
    else:
        running.Close()
        raise RuntimeError("Close Qt Package Tool before reserving the installer QA window.")
    base = delta.load_manifest(base_manifest)
    target = delta.load_manifest(target_bundle / MANIFEST)
    if base["version"] != "1.0.0" or target["version"] != "1.0.1":
        raise ValueError("This local acceptance requires the exact 1.0.0 baseline and an unpublished real 1.0.1 bundle.")
    if delta.encode_manifest(delta.create_manifest(target_bundle, "1.0.1")) != (target_bundle / MANIFEST).read_bytes():
        raise ValueError("The supplied real target bundle differs from its recorded manifest.")
    qt_application = QCoreApplication.instance() or QCoreApplication([])
    ctypes.windll.kernel32.QueryFullProcessImageNameW.argtypes = (
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong))
    ctypes.windll.kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    windows = Path(os.environ["SystemRoot"])
    csc = windows / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    env["LOCALAPPDATA"] = str(work / "localdata")
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
        env.pop(key, None)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0

    def run(command, timeout=240):
        result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env, startupinfo=startup,
                                creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Acceptance command failed ({result.returncode}): {command[0]}\n" + (result.stdout + result.stderr).decode(errors="replace"))
        return result

    # UIA reads the actual compiled Qt window; navigation uses its observed Win32 geometry.
    uia_source = work / "UiReader.cs"
    uia_source.write_text(r'''
using System; using System.Collections.Generic; using System.Windows.Automation; using System.Web.Script.Serialization;
class UiReader { [STAThread] static int Main(string[] args) {
 Console.OutputEncoding=new System.Text.UTF8Encoding(false);
 var root=AutomationElement.RootElement.FindFirst(TreeScope.Children,new PropertyCondition(AutomationElement.ProcessIdProperty,Int32.Parse(args[0])));
 if(root==null)return 3;
 var result=new List<object>();
 foreach(AutomationElement e in root.FindAll(TreeScope.Subtree,Condition.TrueCondition)) {
  var p=e.Current; if(p.IsOffscreen)continue; var r=p.BoundingRectangle;
  result.Add(new{name=p.Name,cls=p.ClassName,x=r.X,y=r.Y,w=r.Width,h=r.Height});
 }
 Console.WriteLine(new JavaScriptSerializer{MaxJsonLength=4194304}.Serialize(result));return 0;
} }''', encoding="utf-8")
    uia = work / "UiReader.exe"
    framework = csc.parent
    run([csc, "/nologo", "/target:exe", "/platform:x64", f"/out:{uia}",
         f"/reference:{framework / 'WPF/UIAutomationClient.dll'}", f"/reference:{framework / 'WPF/UIAutomationTypes.dll'}",
         f"/reference:{framework / 'WPF/WindowsBase.dll'}", "/reference:System.Web.Extensions.dll", uia_source])

    def processes(executable: Path) -> list[int]:
        found = []
        for pid in win32process.EnumProcesses():
            try:
                handle = win32api.OpenProcess(0x1000, False, pid)
                try:
                    name = ctypes.create_unicode_buffer(32768)
                    length = ctypes.c_ulong(len(name))
                    if ctypes.windll.kernel32.QueryFullProcessImageNameW(int(handle), 0, name, ctypes.byref(length)) and Path(name.value) == executable:
                        found.append(pid)
                finally:
                    handle.Close()
            except win32api.error:
                pass
        return found

    def window(pid: int):
        matches = []
        win32gui.EnumWindows(lambda hwnd, _: matches.append(hwnd) if win32process.GetWindowThreadProcessId(hwnd)[1] == pid
                             and win32gui.GetWindowText(hwnd) == "Qt Package Tool" else None, None)
        return matches[0] if matches else None

    def close(pid: int) -> None:
        hwnd = window(pid)
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid)
        except win32api.error as error:
            if error.winerror == 87:
                return
            raise
        try:
            if win32event.WaitForSingleObject(handle, 15000) != win32event.WAIT_OBJECT_0:
                raise RuntimeError("An owned Qt GUI did not exit normally; it was not forcefully terminated.")
        finally:
            handle.Close()

    def about(pid: int, version: str, case: Path, name: str) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                data = json.loads(run([uia, str(pid)], timeout=15).stdout.decode("utf-8-sig"))
                nodes = sorted((entry for entry in data if entry["cls"] == "NavigationTreeWidget"), key=lambda entry: entry["y"])
                if len(nodes) == 4:
                    break
            except RuntimeError:
                pass
            time.sleep(.4)
        else:
            raise RuntimeError("The actual Qt window did not expose its navigation through UIA.")
        node = nodes[3]
        hwnd = window(pid)
        x, y = win32gui.ScreenToClient(hwnd, (round(node["x"] + node["w"] / 2), round(node["y"] + node["h"] / 2)))
        position = win32api.MAKELONG(x, y)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, position)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, position)
        time.sleep(.5)
        data = json.loads(run([uia, str(pid)], timeout=15).stdout.decode("utf-8-sig"))
        (case / (name + "-uia.json")).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        ImageGrab.grab(bbox=win32gui.GetWindowRect(hwnd)).save(case / (name + ".png"))
        assert any(entry["name"] == version for entry in data), (name, version, data)

    # Add one harmless QA-owned file only to the target copy, exercising real added-file ownership.
    bundle = work / "target-bundle"
    shutil.copytree(target_bundle, bundle, copy_function=shutil.copyfile)
    extra = bundle / "qa-only/update-marker.txt"
    extra.parent.mkdir()
    extra.write_bytes(b"Unpublished local Qt update acceptance payload\n")
    target_manifest = delta.write_manifest(bundle, "1.0.1")
    values = runpy.run_path(str(ROOT / "src/app/metadata.py"))
    values["APP_VERSION"] = "1.0.1"
    output = work / "installers"
    output.mkdir()
    full = delta.compile_installer(bundle, values, iscc, output)
    archive = delta.build_delta(base_manifest, target_manifest, bundle, values, iscc, output)
    with zipfile.ZipFile(archive) as stream:
        entries = {name: stream.read(name) for name in stream.namelist()}
    failure_source = work / "PartialInstaller.cs"
    failure_source.write_text(r'''
using System; using System.IO;
class PartialInstaller { static int Main(string[] args) {
 string app=null; foreach(string arg in args) if(arg.StartsWith("/DIR="))app=arg.Substring(5);
 if(app==null)return 99;
 File.WriteAllText(Path.Combine(app,"resource/config/qt_dependencies.json"),"controlled partial installer failure");
 return 9;
} }''', encoding="utf-8")
    partial = work / "PartialInstaller.exe"
    run([csc, "/nologo", "/target:winexe", "/platform:x64", f"/out:{partial}", failure_source])
    owned_root = Path(tempfile.mkdtemp(prefix="QtPackageTool-real-update-"))
    originals = {UNINSTALL: snapshot(UNINSTALL), SETTINGS: snapshot(SETTINGS)}
    settings_parent_existed = snapshot(r"Software\QtPackageTool") is not None
    shortcuts = {}
    for folder in (shellcon.CSIDL_PROGRAMS, shellcon.CSIDL_DESKTOPDIRECTORY):
        shortcut = Path(shell.SHGetFolderPath(0, folder, 0, 0)) / (values["APP_NAME"] + ".lnk")
        shortcuts[shortcut] = shortcut.read_bytes() if shortcut.is_file() else None
    (work / "registry-before.json").write_text(json.dumps(originals, indent=2, ensure_ascii=False,
        default=lambda value: {"bytes_base64": base64.b64encode(value).decode("ascii")}), encoding="utf-8")
    (work / "shortcuts-before.json").write_text(json.dumps({
        str(path): base64.b64encode(data).decode("ascii") if data is not None else None
        for path, data in shortcuts.items()
    }, indent=2), encoding="utf-8")
    checks = []
    active_install = None
    active_worker = None
    held_file = None

    def uninstall(install: Path):
        for pid in processes(install / "QtPackageTool.exe"):
            close(pid)
        if (install / "unins000.exe").is_file():
            run([install / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={work / ('uninstall-' + install.parent.name + '.log')}"])

    def execute_case(name, *, corrupt=False, partial_delta=False, partial_full=False, lock=False, conflict=False):
        nonlocal active_install, active_worker, held_file
        case = work / name
        case.mkdir()
        install = owned_root / name / "应用 安装"
        cache = owned_root / name / "cache/download"
        cache.mkdir(parents=True)
        settings = QSettings("QtPackageTool", "QtPackageTool")
        for key, value in {"language": "en", "theme": "light", "build/jobs": 4, "realUpdateAcceptance": "preserve settings",
                           "updates/autoDownload": False, "updates/lastCheck": datetime.now(timezone.utc).isoformat()}.items():
            settings.setValue(key, value)
        settings.sync()
        run([base_setup, "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/RESTARTAPP=0", f"/DIR={install}", f"/LOG={case / 'baseline-install.log'}"])
        active_install = install
        assert (install / MANIFEST).read_bytes() == base_manifest.read_bytes()
        user_file = install / "user-owned.txt"
        user_file.write_bytes(b"preserve unrelated user file")
        if conflict:
            collision = install / "qa-only/update-marker.txt"
            collision.parent.mkdir()
            collision.write_bytes(b"preserve colliding user file")
        registration = snapshot(UNINSTALL)
        unins = {p.name: sha(p) for p in install.glob("unins*") if p.is_file()}
        old = subprocess.Popen([str(install / "QtPackageTool.exe")], cwd=install, env=env)
        about(old.pid, "1.0.0", case, "baseline-about")
        downloaded = cache / archive.name
        if partial_delta:
            with zipfile.ZipFile(downloaded, "w", compression=zipfile.ZIP_STORED) as stream:
                for entry, data in entries.items():
                    stream.writestr(entry, partial.read_bytes() if entry == "installer.exe" else data)
        else:
            shutil.copyfile(archive, downloaded)
        expected_size, expected_hash = downloaded.stat().st_size, sha(downloaded)
        if corrupt:
            image = bytearray(downloaded.read_bytes())
            image[len(image) // 2] ^= 1
            downloaded.write_bytes(image)
        selected_full = partial if partial_full else full
        shutil.copyfile(selected_full, cache / INSTALLER)
        shutil.copyfile(target_manifest, cache / MANIFEST)
        url = "https://github.com/NCFXZ/QtPackageTool/releases/download/v1.0.1/"
        release = Release("1.0.1", "Local acceptance only", url + INSTALLER, selected_full.stat().st_size, sha(selected_full),
                          Asset(downloaded.name, url + downloaded.name, expected_size, expected_hash),
                          Asset(MANIFEST, url + MANIFEST, target_manifest.stat().st_size, sha(target_manifest)))
        inherited = {key: os.environ.get(key) for key in set(env) | set(os.environ)}
        try:
            # The detached product worker must inherit the same isolated logs and system PATH as its Qt GUI.
            os.environ.clear()
            os.environ.update(env)
            assert launch_delta_update(downloaded, release, install, old.pid)
        finally:
            os.environ.clear()
            os.environ.update({key: value for key, value in inherited.items() if value is not None})
        active_worker = cache / HELPER
        # A freshly copied helper may require a first-run framework/security scan.
        # Observe its production checkpoint before asking the old GUI to exit.
        deadline = time.monotonic() + 60
        workers = []
        waiting_seen = False
        while time.monotonic() < deadline:
            workers = processes(cache / HELPER)
            state = cache / "helper-state.json"
            try:
                phase = json.loads(state.read_text(encoding="utf-8"))["phase"]
            except (OSError, ValueError):
                phase = None
            if workers and phase == "waiting":
                waiting_seen = True
                break
            time.sleep(.1)
        assert waiting_seen and len(workers) == 1, {
            "error": "The product handoff did not start its installed helper copy and wait",
            "workers": workers, "phase": phase, "cache": str(cache),
        }
        assert old.poll() is None and (install / MANIFEST).read_bytes() == base_manifest.read_bytes()
        time.sleep(.5)
        if lock:
            held_file = win32file.CreateFile(str(install / "QtPackageTool.exe"), win32con.GENERIC_READ,
                win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None)
        close(old.pid)
        assert old.wait(timeout=10) == 0
        result_path = cache / "helper-result.json"
        deadline = time.monotonic() + 300
        while processes(cache / HELPER) and time.monotonic() < deadline:
            try:
                log = (cache / "helper.log").read_text(encoding="utf-8")
            except OSError:
                log = ""
            if lock and held_file is not None and "rollback_failed_before_full" in log:
                held_file.Close()
                held_file = None
            try:
                pending_result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pending_result = None
            if pending_result is not None and not pending_result["success"]:
                close_own_failure_dialog(workers[0])
            time.sleep(.1)
        if processes(cache / HELPER):
            raise RuntimeError("The owned updater did not exit; it was not forcefully terminated: " + str(cache))
        active_worker = None
        if held_file is not None:
            held_file.Close()
            held_file = None
        result = json.loads(result_path.read_text(encoding="utf-8"))
        log = (cache / "helper.log").read_text(encoding="utf-8")
        expected_failure = conflict or partial_full
        assert result["success"] != expected_failure, result
        expected_manifest = base if expected_failure else delta.load_manifest(target_manifest)
        for entry in expected_manifest["files"]:
            assert sha(install / entry["path"]) == entry["sha256"], entry["path"]
        if expected_failure:
            assert (install / MANIFEST).read_bytes() == base_manifest.read_bytes()
            assert snapshot(UNINSTALL) == registration
            assert {p.name: sha(p) for p in install.glob("unins*") if p.is_file()} == unins
            assert not (install / "qa-only/update-marker.txt").exists() or conflict
            if conflict:
                assert collision.read_bytes() == b"preserve colliding user file"
                assert "installer_starting" not in log and " | restoring | " not in log
            else:
                assert "rollback_complete" in log
            current = subprocess.Popen([str(install / "QtPackageTool.exe")], cwd=install, env=env)
            new_pid = current.pid
        else:
            deadline = time.monotonic() + 20
            candidates = []
            while time.monotonic() < deadline:
                candidates = processes(install / "QtPackageTool.exe")
                if candidates:
                    break
                time.sleep(.2)
            assert len(candidates) == 1, candidates
            new_pid = candidates[0]
            assert result["mode"] == ("full" if corrupt or partial_delta or lock else "delta")
        about(new_pid, "1.0.0" if expected_failure else "1.0.1", case, "result-about")
        close(new_pid)
        settings.sync()
        for key, expected in {"realUpdateAcceptance": "preserve settings", "language": "en", "theme": "light", "build/jobs": 4}.items():
            assert settings.value(key, type=type(expected)) == expected, key
        assert user_file.read_bytes() == b"preserve unrelated user file"
        if corrupt or partial_delta or lock:
            assert log.count(" | full_fallback | ") == 1
            assert log.count(" | full_installer_starting | ") == (0 if conflict else 1)
        if lock:
            assert "rollback_failed_before_full" in log, "The lock did not exercise the intended recovery branch"
        uninstall(install)
        active_install = None
        assert not (install / "QtPackageTool.exe").exists()
        assert not (install / "qa-only/update-marker.txt").exists() or conflict
        assert user_file.exists()
        evidence = case / "worker"
        evidence.mkdir()
        for record in cache.iterdir():
            if record.is_file() and record.suffix in (".json", ".log"):
                shutil.copyfile(record, evidence / record.name)
        checks.append({"case": name, "result": result, "gui_version": "1.0.0" if expected_failure else "1.0.1",
                       "settings_preserved": True, "normal_close": True, "uninstall": True})
        (work / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
        print("PASS:", name, flush=True)

    try:
        execute_case("real-delta-normal-and-settings")
        execute_case("real-delta-corrupt-verified-full-recovery", corrupt=True)
        execute_case("real-partial-delta-verified-full-recovery", partial_delta=True)
        execute_case("real-partial-delta-and-full-failure-restore", partial_delta=True, partial_full=True)
        execute_case("real-locked-executable-full-recovery", lock=True)
        execute_case("real-user-collision-safe-stop", conflict=True)
        execute_case("real-corrupt-delta-user-collision-safe-stop", corrupt=True, conflict=True)
    finally:
        if held_file is not None:
            held_file.Close()
        cleanup_error = None
        if active_worker is not None and processes(active_worker):
            # An installer may still be writing. Never race it with uninstall or registry restoration.
            (work / "cleanup-blocked.json").write_text(json.dumps({
                "active_worker": str(active_worker), "active_install": str(active_install),
                "registry_restored": False, "reason": "Owned updater is still active; no forceful termination or concurrent restore"}, indent=2), encoding="utf-8")
            raise RuntimeError("The owned updater remains active; safe cleanup requires its normal completion. See " + str(work))
        try:
            if active_install is not None:
                uninstall(active_install)
        except Exception as error:
            cleanup_error = str(error)
        for key, saved in originals.items():
            restore(key, saved)
            assert snapshot(key) == saved
        if not settings_parent_existed and snapshot(r"Software\QtPackageTool") == {"values": [], "children": {}}:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, r"Software\QtPackageTool", winreg.KEY_WOW64_64KEY)
        for shortcut, saved in shortcuts.items():
            if saved is None:
                shortcut.unlink(missing_ok=True)
            else:
                shortcut.parent.mkdir(parents=True, exist_ok=True)
                shortcut.write_bytes(saved)
        summary = {"checks": checks, "baseline_installer_sha256": sha(base_setup), "baseline_manifest_sha256": sha(base_manifest),
                   "target_executable_sha256": sha(bundle / "QtPackageTool.exe"), "target_manifest_sha256": sha(target_manifest),
                   "helper_source_sha256": sha(ROOT / "packaging/updater/Program.cs"), "user_registry_restored": True,
                   "cleanup_error": cleanup_error, "native_fixture_root": str(owned_root),
                   "host_python": sys.version, "host_expat": __import__("pyexpat").EXPAT_VERSION,
                   "target_build_record": json.loads((target_bundle.parent / "standalone-build.json").read_text(encoding="utf-8"))
                       if (target_bundle.parent / "standalone-build.json").is_file() else None,
                   "scope": {"binaries": "Real Nuitka QtPackageTool 1.0.0 and unpublished 1.0.1; target has one harmless QA-owned added file",
                             "handoff": "Production launch_delta_update called by the source acceptance host; normal compiled GUI exit and actual installed helper",
                             "network": "Preseeded fixed-name full/manifest cache verified by production hashes; no live 1.0.1 GitHub release",
                             "faults": "Corrupt ZIP, actual Windows EXE lock, and controlled failing installer replacing only test payload"}}
        (work / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print("User state restored; evidence:", work, flush=True)
        if cleanup_error:
            raise RuntimeError("Installer cleanup failed after restoring user registry: " + cleanup_error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--safety-only", action="store_true", help="Run read-only production helper preflight regression")
    parser.add_argument("--baseline-installer", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--target-bundle", type=Path)
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    if args.safety_only:
        work = Path(tempfile.mkdtemp(prefix="acceptance-update-safety-", dir=ROOT / "logs"))
        safety_checks(work)
        print("Evidence:", work, flush=True)
    else:
        if not all((args.baseline_installer, args.baseline_manifest, args.target_bundle, args.iscc)):
            parser.error("Real installed acceptance requires --baseline-installer, --baseline-manifest, --target-bundle and --iscc")
        work = Path(tempfile.mkdtemp(prefix="acceptance-qt-update-installed-", dir=ROOT / "logs"))
        installed_checks(work, *(path.resolve(strict=True) for path in
            (args.baseline_installer, args.baseline_manifest, args.target_bundle, args.iscc)))


if __name__ == "__main__":
    main()
