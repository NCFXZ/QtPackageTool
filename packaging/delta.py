# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict file manifests and Inno-based incremental release artifacts.

This module uses only the standard library so small installer fixtures can use
the same generator as the full application build.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "QtPackageTool-files.json"
MAX_MANIFEST_SIZE = 1024 * 1024
MAX_FILES = 10000
MAX_FILE_SIZE = 2 * 1024**3
MAX_TOTAL_SIZE = 4 * 1024**3
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_UNINSTALLER = re.compile(r"unins[0-9]+\.(exe|dat|msg)\Z", re.IGNORECASE)
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.IGNORECASE)


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("A stable three-component release version is required.")
    parts = tuple(map(int, value.split(".")))
    if max(parts) > 65535:
        raise ValueError("Release version components must fit Windows version resources.")
    return parts


def validate_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("Invalid managed relative path.")
    if any(ord(char) < 32 or char in '\\<>:"|?*' for char in value) or "{#" in value:
        raise ValueError(f"Unsafe managed path: {value!r}")
    for part in value.split("/"):
        if not part or part in (".", "..") or part.endswith((" ", ".")) or _RESERVED.fullmatch(part.split(".")[0]):
            raise ValueError(f"Unsafe managed path: {value!r}")
    return value


def _excluded(relative: str) -> bool:
    return relative.casefold() == MANIFEST_NAME.casefold() or ("/" not in relative and bool(_UNINSTALLER.fullmatch(relative)))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_manifest(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != {"schema", "version", "files"} or type(data["schema"]) is not int or data["schema"] != 1:
        raise ValueError("Unsupported file manifest schema.")
    version_tuple(data["version"])
    files = data["files"]
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ValueError("Invalid manifest file count.")
    paths = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("Invalid manifest file entry.")
        relative = validate_path(item["path"])
        folded = relative.casefold()
        if _excluded(relative) or folded in paths:
            raise ValueError("Duplicate or reserved managed file.")
        if type(item["size"]) is not int or not 0 <= item["size"] <= MAX_FILE_SIZE:
            raise ValueError("Invalid managed file size.")
        if not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]):
            raise ValueError("Invalid managed file SHA256.")
        paths.add(folded)
        total += item["size"]
    if total > MAX_TOTAL_SIZE:
        raise ValueError("Managed files exceed the total size limit.")
    for relative in paths:
        parts = relative.split("/")
        if any("/".join(parts[:index]) in paths for index in range(1, len(parts))):
            raise ValueError("A managed file is also used as a directory.")
    return data


def encode_manifest(data: dict) -> bytes:
    validate_manifest(data)
    result = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(result) > MAX_MANIFEST_SIZE:
        raise ValueError("File manifest exceeds 1 MiB.")
    return result


def _decode_manifest(content: bytes) -> dict:
    if len(content) > MAX_MANIFEST_SIZE:
        raise ValueError("File manifest exceeds 1 MiB.")
    data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    return validate_manifest(data)


def load_manifest(path: Path) -> dict:
    with path.open("rb") as stream:
        return _decode_manifest(stream.read(MAX_MANIFEST_SIZE + 1))


def snapshot_manifest(path: Path, output_dir: Path) -> Path:
    """Pin exact old bytes before a rebuild overwrites exported build outputs."""
    with path.open("rb") as stream:
        content = stream.read(MAX_MANIFEST_SIZE + 1)
    _decode_manifest(content)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / ("base-" + hashlib.sha256(content).hexdigest() + ".json")
    if destination.exists():
        if destination.read_bytes() != content:
            raise ValueError("The pinned baseline manifest was modified.")
    else:
        with destination.open("xb") as stream:
            stream.write(content)
    return destination


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _source_file(bundle: Path, relative: str) -> Path:
    """Reject redirecting links while permitting hydrated OneDrive source files."""
    relative = validate_path(relative)
    path = bundle
    for part in relative.split("/"):
        path /= part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
            raise ValueError(f"Source file has a redirecting reparse parent: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(bundle.resolve()):
        raise ValueError(f"Invalid source file: {relative}")
    return path


def create_manifest(bundle: Path, version: str) -> dict:
    version_tuple(version)
    files = []
    for directory, directories, names in os.walk(bundle, followlinks=False):
        for name in directories:
            info = (Path(directory) / name).lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
                raise ValueError("Standalone contains a redirecting directory.")
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(bundle).as_posix()
            if not _excluded(relative):
                path = _source_file(bundle, relative)
                files.append({"path": relative, "size": path.stat().st_size, "sha256": file_sha256(path)})
                if len(files) > MAX_FILES:
                    raise ValueError("Too many standalone files.")
    files.sort(key=lambda item: item["path"].casefold())
    manifest = {"schema": 1, "version": version, "files": files}
    encode_manifest(manifest)
    return manifest


def write_manifest(bundle: Path, version: str, destination: Path | None = None) -> Path:
    destination = destination or bundle / MANIFEST_NAME
    destination.write_bytes(encode_manifest(create_manifest(bundle, version)))
    return destination


def diff_manifests(base: dict, target: dict) -> tuple[list[str], list[str]]:
    validate_manifest(base)
    validate_manifest(target)
    old = {item["path"].casefold(): item for item in base["files"]}
    new = {item["path"].casefold(): item for item in target["files"]}
    changed = [item["path"] for key, item in new.items() if key not in old or (item["size"], item["sha256"]) != (old[key]["size"], old[key]["sha256"])]
    removed = [item["path"] for key, item in old.items() if key not in new]
    return sorted(changed, key=str.casefold), sorted(removed, key=str.casefold)


def compile_installer(bundle: Path, values: dict, iscc: Path, output_dir: Path, *, output_name: str | None = None, delta_entries: Path | None = None, base_manifest_sha256: str | None = None) -> Path:
    version_tuple(values["APP_VERSION"])
    output_name = output_name or values["INSTALLER_NAME"]
    if Path(output_name).name != output_name or not output_name.endswith(".exe"):
        raise ValueError("Installer output must be a plain EXE filename.")
    if (delta_entries is None) != (base_manifest_sha256 is None):
        raise ValueError("Delta entries and exact base SHA must be supplied together.")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [str(iscc), "/Qp", f"/DAppVersion={values['APP_VERSION']}", f"/DAppName={values['APP_NAME']}",
               f"/DRepository={values['REPOSITORY']}", f"/DApplicationMutex={values['APPLICATION_MUTEX']}",
               f"/DFileManifestName={MANIFEST_NAME}", f"/DInstallerBaseName={Path(output_name).stem}",
               f"/DDistDir={bundle.resolve()}", f"/DOutputDir={output_dir.resolve()}"]
    if delta_entries is not None:
        if not _SHA256.fullmatch(base_manifest_sha256):
            raise ValueError("Invalid base manifest SHA256.")
        command.extend(("/DDeltaUpdate=1", f"/DDeltaEntries={delta_entries.resolve()}", f"/DBaseManifestSHA={base_manifest_sha256}"))
    command.append(str(ROOT / "packaging" / "QtPackageTool.iss"))
    subprocess.run(command, cwd=ROOT, check=True)
    result = output_dir / output_name
    if not result.is_file():
        raise FileNotFoundError(result)
    return result


def _inno_quote(value: str, *, runtime: bool = False) -> str:
    if "{#" in value or any(char in value for char in '\r\n\0'):
        raise ValueError("Unsafe Inno path.")
    if runtime:
        value = value.replace("{", "{{")
    return '"' + value.replace('"', '""') + '"'


def build_delta(base_manifest: Path, target_manifest: Path, bundle: Path, values: dict, iscc: Path, output_dir: Path) -> Path:
    base, target = load_manifest(base_manifest), load_manifest(target_manifest)
    if version_tuple(base["version"]) >= version_tuple(target["version"]) or target["version"] != values["APP_VERSION"]:
        raise ValueError("Delta requires an older stable base and the current target version.")
    changed, removed = diff_manifests(base, target)
    # A file/directory replacement can otherwise affect untracked user files.
    old = {item["path"].casefold() for item in base["files"]}
    new = {item["path"].casefold() for item in target["files"]}
    for left, right in ((old, new), (new, old)):
        if any("/".join(path.split("/")[:index]) in left for path in right for index in range(1, len(path.split("/")))):
            raise ValueError("Delta cannot replace a file with a directory; distribute the full installer.")
    for item in target["files"]:
        source = _source_file(bundle, item["path"])
        if source.stat().st_size != item["size"] or file_sha256(source) != item["sha256"]:
            raise ValueError(f"Target bundle differs from its manifest: {item['path']}")
    base_bytes, target_bytes = base_manifest.read_bytes(), target_manifest.read_bytes()
    if (bundle / MANIFEST_NAME).read_bytes() != target_bytes:
        raise ValueError("The bundled target manifest must exactly match the release manifest.")
    work = output_dir / f"delta-build-{base['version']}-to-{target['version']}"
    work.mkdir(parents=True, exist_ok=True)
    lines = ["; Generated exact managed file changes.", "[Files]"]
    for relative in [*changed, MANIFEST_NAME]:
        source = _source_file(bundle, relative)
        parts = relative.replace("/", "\\").rsplit("\\", 1)
        parent, name = (parts if len(parts) == 2 else ("", parts[0]))
        destination = '"{app}' + ("\\" + parent.replace("{", "{{") if parent else "") + '"'
        lines.append(f"Source: {_inno_quote(str(source))}; DestDir: {destination}; DestName: {_inno_quote(name, runtime=True)}; Flags: ignoreversion")
    lines.append("[InstallDelete]")
    for relative in removed:
        path = relative.replace("/", "\\").replace("{", "{{")
        lines.append(f'Type: files; Name: "{{app}}\\{path}"')
    entries = work / "entries.iss"
    entries.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    installer = compile_installer(bundle, values, iscc, work, output_name="installer.exe", delta_entries=entries, base_manifest_sha256=hashlib.sha256(base_bytes).hexdigest())
    if not 0 < installer.stat().st_size <= MAX_FILE_SIZE:
        raise ValueError("The incremental installer exceeds the helper's size limit.")
    metadata = {"schema": 1, "base_version": base["version"], "base_manifest_sha256": hashlib.sha256(base_bytes).hexdigest(),
                "target_version": target["version"], "target_manifest_sha256": hashlib.sha256(target_bytes).hexdigest()}
    archive = output_dir / f"QtPackageTool-Update-{base['version']}-to-{target['version']}-x64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as stream:
        stream.writestr("delta.json", json.dumps(metadata, separators=(",", ":")) + "\n")
        stream.writestr("files.json", target_bytes)
        stream.write(installer, "installer.exe")
    return archive
