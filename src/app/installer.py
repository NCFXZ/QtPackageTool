# SPDX-License-Identifier: GPL-3.0-or-later

"""Hand a verified update to the installer without invoking a shell."""

import hashlib
import json
from pathlib import Path
import stat

from PyQt6.QtCore import QProcess

from app.metadata import APP_VERSION, FILE_MANIFEST_NAME, UPDATER_NAME
from app.localization import tr
from app.updates.eligibility import delta_runtime_available


def launch_installer(installer: Path, app_dir: Path, process_id: int) -> bool:
    arguments = [
        "/SP-", "/SILENT", "/NORESTART", "/RESTARTAPP=1",
        f"/WAITPID={process_id}", f"/DIR={app_dir}",
    ]
    started, _ = QProcess.startDetached(str(installer), arguments, str(installer.parent))
    return started


def launch_delta_update(archive: Path, release, app_dir: Path, process_id: int) -> bool:
    """Run an independently copied helper so installed binaries can be replaced."""
    if not delta_runtime_available():
        raise ValueError("The incremental update runtime is unavailable.")
    app_dir, archive = app_dir.absolute(), archive.absolute()
    work = archive.parent
    if work == app_dir or work.is_relative_to(app_dir):
        raise ValueError("The update helper must run outside the installed application.")
    for parent in (work, *work.parents, app_dir, *app_dir.parents):
        if parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("An update directory cannot be a reparse point.")
    manifest_path = app_dir / FILE_MANIFEST_NAME
    manifest_info = manifest_path.lstat()
    if (
        not stat.S_ISREG(manifest_info.st_mode)
        or manifest_info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or manifest_info.st_size > 1024 * 1024
    ):
        raise ValueError("The installed file manifest is invalid.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except RecursionError as error:
        raise ValueError("The installed file manifest is invalid.") from error
    if (
        not isinstance(manifest, dict) or type(manifest.get("schema")) is not int
        or manifest["schema"] != 1 or manifest.get("version") != APP_VERSION
    ):
        raise ValueError("The installed file manifest does not match this version.")
    entries = manifest.get("files")
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("path") == UPDATER_NAME] if isinstance(entries, list) else []
    if len(matches) != 1 or release.delta is None or getattr(release, "file_manifest", None) is None:
        raise ValueError("The incremental update helper is unavailable.")
    helper_source = app_dir / UPDATER_NAME
    info = helper_source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("The incremental update helper is invalid.")
    helper = work / UPDATER_NAME
    plan_path = work / "update-plan.json"
    created = []
    try:
        digest = hashlib.sha256()
        with helper_source.open("rb") as source, helper.open("xb") as destination:
            created.append(helper)
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                destination.write(chunk)
                digest.update(chunk)
        if helper.stat().st_size != matches[0].get("size") or digest.hexdigest() != matches[0].get("sha256"):
            raise ValueError("The incremental update helper could not be verified.")
        plan = {
            "schema": 1, "app_dir": str(app_dir), "wait_pid": process_id,
            "base_version": APP_VERSION, "target_version": release.version,
            "delta": {"path": str(archive), "size": release.delta.size, "sha256": release.delta.sha256},
            "full": {"url": release.url, "size": release.size, "sha256": release.sha256},
            "manifest": {
                "url": release.file_manifest.url, "size": release.file_manifest.size,
                "sha256": release.file_manifest.sha256,
            },
            "messages": {
                "title": tr("Qt Package Tool Update"),
                "failure": tr("The update could not be completed. Download the full installer to try again."),
                "restart_failure": tr("The update was installed, but the application could not be started. Start Qt Package Tool manually."),
                "diagnostics": tr("Diagnostics"),
                "conflict": tr("The update was stopped to protect existing files in the installation folder. Move the conflicting files or folders elsewhere, then check for updates again."),
            },
        }
        with plan_path.open("x", encoding="utf-8") as stream:
            created.append(plan_path)
            json.dump(plan, stream, ensure_ascii=False)
        started, _ = QProcess.startDetached(str(helper), ["--plan", str(plan_path)], str(work))
        if started:
            return True
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    for path in reversed(created):
        path.unlink(missing_ok=True)
    return False
