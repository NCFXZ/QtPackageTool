# SPDX-License-Identifier: GPL-3.0-or-later

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import delta


class IncrementalPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        (self.bundle / "QtPackageTool.exe").write_bytes(b"MZ-baseline")
        self.values = {"APP_VERSION": "1.0.1", "APP_NAME": "Qt Package Tool", "REPOSITORY": "NCFXZ/QtPackageTool",
                       "APPLICATION_MUTEX": "QtPackageTool.Application", "INSTALLER_NAME": "QtPackageTool-Setup-x64.exe"}

    def manifest(self):
        return delta.create_manifest(self.bundle, "1.0.0")

    def test_manifest_deterministic_and_excludes_installer_bookkeeping(self):
        (self.bundle / delta.MANIFEST_NAME).write_bytes(b"old manifest")
        (self.bundle / "unins000.exe").write_bytes(b"uninstaller")
        (self.bundle / "UNINS001.DAT").write_bytes(b"uninstaller log")
        first = delta.encode_manifest(self.manifest())
        second = delta.encode_manifest(self.manifest())
        self.assertEqual(first, second)
        self.assertEqual([item["path"] for item in json.loads(first)["files"]], ["QtPackageTool.exe"])
        self.assertTrue(first.endswith(b"\n"))

    def test_paths_reject_windows_aliases_and_traversal(self):
        for path in ("../x", "/x", "a//b", "a/./b", "a\\b", "C:/x", "a:stream", "con", "COM1.txt",
                     "lpt².dll", "CON .txt", "AUX .json", "COM1 .log", "LPT¹ .txt",
                     "a./b", "a /b", "a\x00b", "a?b", "a/{#Injected}"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                delta.validate_path(path)
        self.assertEqual(delta.validate_path("resource/中文 space/{literal}.json"), "resource/中文 space/{literal}.json")

    def test_case_insensitive_duplicate_and_parent_file_rejected(self):
        original = self.manifest()
        for path in ("qtpackagetool.EXE", "QtPackageTool.exe/child"):
            data = copy.deepcopy(original)
            data["files"].append({**data["files"][0], "path": path})
            with self.subTest(path=path), self.assertRaises(ValueError):
                delta.validate_manifest(data)

    def test_size_hash_schema_and_version_are_strict(self):
        original = self.manifest()
        for field, value in (("size", True), ("size", -1), ("size", delta.MAX_FILE_SIZE + 1),
                             ("sha256", "A" * 64), ("sha256", "abc")):
            data = copy.deepcopy(original)
            data["files"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                delta.validate_manifest(data)
        for version in ("v1.0.0", "1.0.0-beta", "01.0.0", "1.0", "65536.0.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                delta.version_tuple(version)
        for schema in (True, 2, "1"):
            data = {**original, "schema": schema}
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                delta.validate_manifest(data)

    def test_duplicate_json_keys_and_oversize_manifest_rejected(self):
        path = self.root / "manifest.json"
        path.write_text('{"schema":1,"schema":1,"version":"1.0.0","files":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            delta.load_manifest(path)
        path.write_bytes(b" " * (delta.MAX_MANIFEST_SIZE + 1))
        with self.assertRaises(ValueError):
            delta.load_manifest(path)

    def test_aggregate_size_limit(self):
        data = self.manifest()
        item = data["files"][0]
        data["files"] = [{**item, "path": f"large{i}", "size": delta.MAX_FILE_SIZE} for i in range(3)]
        with self.assertRaises(ValueError):
            delta.validate_manifest(data)

    def test_diff_hashes_sizes_and_preserves_unchanged_case(self):
        base = self.manifest()
        target = copy.deepcopy(base)
        target["version"] = "1.0.1"
        target["files"][0]["path"] = "qtpackagetool.EXE"
        self.assertEqual(delta.diff_manifests(base, target), ([], []))
        target["files"][0]["size"] += 1
        self.assertEqual(delta.diff_manifests(base, target), (["qtpackagetool.EXE"], []))

    def _delta_fixture(self):
        (self.bundle / "removed.dll").write_bytes(b"removed")
        (self.bundle / "unchanged.dll").write_bytes(b"same")
        base = self.root / "base.json"
        delta.write_manifest(self.bundle, "1.0.0", base)
        (self.bundle / "removed.dll").unlink()
        (self.bundle / "QtPackageTool.exe").write_bytes(b"MZ-target")
        (self.bundle / "new 中文.dll").write_bytes(b"added")
        target = delta.write_manifest(self.bundle, "1.0.1")
        return base, target

    def _fake_compiler(self, bundle, values, iscc, output_dir, **kwargs):
        result = output_dir / kwargs["output_name"]
        result.write_bytes(b"MZ delta fixture")
        return result

    def test_zip_exact_entries_hashes_and_safe_managed_changes(self):
        base, target = self._delta_fixture()
        # Hashes refer to original byte streams, including externally formatted manifests.
        base.write_text(json.dumps(delta.load_manifest(base), indent=4), encoding="utf-8")
        with patch.object(delta, "compile_installer", side_effect=self._fake_compiler) as compiler:
            archive = delta.build_delta(base, target, self.bundle, self.values, Path("ISCC.exe"), self.root)
        with zipfile.ZipFile(archive) as stream:
            self.assertEqual(stream.namelist(), ["delta.json", "files.json", "installer.exe"])
            metadata = json.loads(stream.read("delta.json"))
            self.assertEqual(metadata["base_manifest_sha256"], hashlib.sha256(base.read_bytes()).hexdigest())
            self.assertEqual(metadata["target_manifest_sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(stream.read("files.json"), target.read_bytes())
        entries = compiler.call_args.kwargs["delta_entries"].read_text(encoding="utf-8-sig")
        self.assertIn('Name: "{app}\\removed.dll"', entries)
        self.assertIn("new 中文.dll", entries)
        self.assertNotIn("unchanged.dll", entries)
        self.assertNotIn("filesandordirs", entries)
        self.assertIn(delta.MANIFEST_NAME, entries)

    def test_delta_rejects_tampered_target_and_non_older_base(self):
        base, target = self._delta_fixture()
        (self.bundle / "new 中文.dll").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "differs"):
            delta.build_delta(base, target, self.bundle, self.values, Path("ISCC.exe"), self.root)
        with self.assertRaisesRegex(ValueError, "older stable"):
            delta.build_delta(target, target, self.bundle, self.values, Path("ISCC.exe"), self.root)

    def test_literal_inno_constants_stay_below_app_directory(self):
        folder = self.bundle / "{win}"
        folder.mkdir()
        (folder / "old.dll").write_bytes(b"old")
        base = self.root / "base.json"
        delta.write_manifest(self.bundle, "1.0.0", base)
        (folder / "old.dll").unlink()
        (folder / "{sys}.dll").write_bytes(b"new")
        target = delta.write_manifest(self.bundle, "1.0.1")
        with patch.object(delta, "compile_installer", side_effect=self._fake_compiler) as compiler:
            delta.build_delta(base, target, self.bundle, self.values, Path("ISCC.exe"), self.root)
        entries = compiler.call_args.kwargs["delta_entries"].read_text(encoding="utf-8-sig")
        self.assertIn('DestDir: "{app}\\{{win}"', entries)
        self.assertIn('DestName: "{{sys}.dll"', entries)
        self.assertIn('Name: "{app}\\{{win}\\old.dll"', entries)

    def test_exported_baseline_is_pinned_before_target_overwrites_its_path(self):
        export = self.root / delta.MANIFEST_NAME
        delta.write_manifest(self.bundle, "1.0.0", export)
        original = export.read_bytes()
        snapshot = delta.snapshot_manifest(export, self.root / "delta-inputs")
        (self.bundle / "QtPackageTool.exe").write_bytes(b"MZ changed")
        target = delta.write_manifest(self.bundle, "1.0.1")
        export.write_bytes(target.read_bytes())
        with patch.object(delta, "compile_installer", side_effect=self._fake_compiler):
            archive = delta.build_delta(snapshot, export, self.bundle, self.values, Path("ISCC.exe"), self.root)
        self.assertEqual(snapshot.read_bytes(), original)
        with zipfile.ZipFile(archive) as stream:
            metadata = json.loads(stream.read("delta.json"))
            self.assertEqual(metadata["base_version"], "1.0.0")
            self.assertEqual(metadata["base_manifest_sha256"], hashlib.sha256(original).hexdigest())

    def test_delta_rejects_file_directory_replacements(self):
        base = self.root / "base.json"
        delta.write_manifest(self.bundle, "1.0.0", base)
        (self.bundle / "QtPackageTool.exe").unlink()
        (self.bundle / "QtPackageTool.exe").mkdir()
        (self.bundle / "QtPackageTool.exe" / "child").write_bytes(b"unsafe replacement")
        target = delta.write_manifest(self.bundle, "1.0.1")
        with self.assertRaisesRegex(ValueError, "replace a file"):
            delta.build_delta(base, target, self.bundle, self.values, Path("ISCC.exe"), self.root)

    def test_full_payload_installs_only_manifest_owned_files(self):
        (self.bundle / "unins001.exe").write_bytes(b"must never be bundled or overwritten")
        delta.write_manifest(self.bundle, "1.0.1")
        output = self.root / "full-output"
        def compile_fixture(command, **kwargs):
            (output / self.values["INSTALLER_NAME"]).write_bytes(b"MZ fixture")
        with patch.object(delta.subprocess, "run", side_effect=compile_fixture) as compiler:
            delta.compile_installer(self.bundle, self.values, Path("ISCC.exe"), output)
        payload = next(output.glob("*-files.iss")).read_text(encoding="utf-8-sig")
        self.assertIn("QtPackageTool.exe", payload)
        self.assertIn(delta.MANIFEST_NAME, payload)
        self.assertNotIn("unins001.exe", payload)
        self.assertIn("/DAppUserModelID=NCFXZ.QtPackageTool", compiler.call_args.args[0])

    def test_installer_version_must_match_manifest(self):
        delta.write_manifest(self.bundle, "1.0.0")
        with self.assertRaisesRegex(ValueError, "manifest version"):
            delta.compile_installer(self.bundle, self.values, Path("ISCC.exe"), self.root / "full-output")


if __name__ == "__main__":
    unittest.main()
