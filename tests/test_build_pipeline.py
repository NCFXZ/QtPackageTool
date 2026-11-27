# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QLocale, QSettings
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build.process as build_process
import build.compiler as compiler_module
from build.process import BuildProcess
from build.compiler import QtCompiler, Stage, is_error_line
from toolchains.discovery import MSVC, Toolchain, find_build_tool, matching_compiler, msvc_environment, msvc_environment_command, msvc_toolchains, qt_toolchain, toolchains_from_data, toolchains_to_data, vcvars_argument
from UI.ui_preferences import AUTO_JOBS, CPU_COUNT, JOB_CHOICES, AppPreferences


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class FakeProcess:
    """Stands in for BuildProcess: records each tool and lets a hook create its outputs."""

    running = False

    def __init__(self, hooks):
        self.hooks = hooks
        self.started = []

    def start(self, program, args, cwd, env, *, encoding="utf-8"):
        self.started.append((Path(program).name, list(args)))
        hook = self.hooks.get(Path(program).name)
        if hook:
            hook(Path(cwd), list(args))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.qt_bin = self.root / "Qt" / "6.5.3" / "mingw_64" / "bin"
        for name in ("qmake.exe", "windeployqt.exe", "qmlimportscanner.exe"):
            touch(self.qt_bin / name)
        self.mingw_bin = self.root / "Qt" / "Tools" / "mingw1120_64" / "bin"
        touch(self.mingw_bin / "mingw32-make.exe")
        self.output = self.root / "output"
        self.output.mkdir()
        validation = patch.object(compiler_module, "validate_startup_libraries")
        validation.start()
        self.addCleanup(validation.stop)

    def run_build(self, project: Path, hooks: dict, *, jobs=3, release=True, compiler=None):
        builder = QtCompiler()
        builder.process = FakeProcess(hooks)
        result = {}
        builder.finished_signal.connect(lambda path: result.update(path=path))
        builder.error_signal.connect(lambda error: result.update(error=error))
        compiler = compiler or Toolchain(self.mingw_bin, "x86_64")
        builder.compile_qt_project(
            str(project), str(self.qt_bin), compiler, str(self.output), [], release, True, jobs
        )
        stages = []
        while builder._stage is not None:
            stages.append(builder._stage)
            builder.process_finished(0)
        return builder, result, stages

    def test_qmake_project_runs_qmake_make_and_deployment(self):
        project = touch(self.root / "app" / "app.pro")

        def qmake(cwd, args):
            touch(cwd / "Makefile", "DESTDIR_TARGET = release/app.exe\n")
            touch(cwd / ".qt-settings-", "release\n")

        hooks = {"qmake.exe": qmake, "mingw32-make.exe": lambda cwd, args: touch(cwd / "release" / "app.exe")}
        builder, result, stages = self.run_build(project, hooks)

        self.assertNotIn("error", result)
        self.assertEqual(stages, [Stage.QMAKE, Stage.BUILD, Stage.DEPLOY])
        self.assertEqual(builder.process.started[1], ("mingw32-make.exe", ["-f", "Makefile", "-j3"]))
        self.assertEqual(result["path"], self.output / "release")
        self.assertTrue((result["path"] / "app.exe").is_file())
        self.assertEqual([entry.name for entry in self.output.iterdir()], ["release"])

    def write_cmake_reply(self, build: Path, project_name: str) -> None:
        reply = build / ".cmake" / "api" / "v1" / "reply"
        touch(reply / "index-1.json", json.dumps({"objects": [{"kind": "codemodel", "jsonFile": "codemodel.json"}]}))
        targets = {
            "demo.json": {"name": "demo", "type": "EXECUTABLE", "artifacts": [{"path": "demo.exe"}]},
            "helper.json": {"name": "helper", "type": "EXECUTABLE", "artifacts": [{"path": "tools/helper.exe"}]},
            "core.json": {"name": "core", "type": "SHARED_LIBRARY", "artifacts": [{"path": "core.dll"}, {"path": "core.lib"}]},
        }
        for name, target in targets.items():
            touch(reply / name, json.dumps(target))
        touch(reply / "codemodel.json", json.dumps({"configurations": [{
            "name": "Release",
            "projects": [{"name": project_name}],
            "targets": [{"jsonFile": name} for name in targets],
        }]}))

    def cmake_hooks(self, project_name: str):
        cmake = touch(self.root / "Qt" / "Tools" / "CMake_64" / "bin" / "cmake.exe")
        touch(self.root / "Qt" / "Tools" / "Ninja" / "ninja.exe")
        build = {}

        def run_cmake(cwd, args):
            if args[0] == "-S":
                build["dir"] = Path(args[args.index("-B") + 1])
                self.write_cmake_reply(build["dir"], project_name)
            else:
                touch(build["dir"] / "demo.exe")
                touch(build["dir"] / "core.dll")

        return cmake, {"cmake.exe": run_cmake}

    def test_cmake_project_packages_the_project_executable_and_its_dlls(self):
        project = touch(self.root / "demo" / "CMakeLists.txt")
        cmake, hooks = self.cmake_hooks("demo")
        builder, result, stages = self.run_build(project, hooks, jobs=2)

        self.assertNotIn("error", result)
        self.assertEqual(stages, [Stage.CMAKE_CONFIGURE, Stage.BUILD, Stage.DEPLOY])
        configure, build, deploy = builder.process.started
        self.assertIn("Ninja", configure[1])
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", configure[1])
        self.assertIn(f"-DCMAKE_PREFIX_PATH={self.qt_bin.parent.as_posix()}", configure[1])
        self.assertEqual(build[1][-4:], ["--target", "demo", "--parallel", "2"])
        self.assertIn("--qmlimport", deploy[1])
        self.assertEqual(sorted(path.name for path in result["path"].iterdir()), ["core.dll", "demo.exe"])
        self.assertEqual(result["path"], self.output / "release")

    def test_cmake_packages_executables_built_outside_the_build_tree(self):
        project = touch(self.root / "demo" / "CMakeLists.txt")
        touch(self.root / "Qt" / "Tools" / "CMake_64" / "bin" / "cmake.exe")
        touch(self.root / "Qt" / "Tools" / "Ninja" / "ninja.exe")
        output_dir = project.parent / "bin"

        def run_cmake(cwd, args):
            if args[0] == "-S":
                reply = Path(args[args.index("-B") + 1]) / ".cmake" / "api" / "v1" / "reply"
                touch(reply / "index-1.json", json.dumps({"objects": [{"kind": "codemodel", "jsonFile": "codemodel.json"}]}))
                targets = {
                    "demo.json": {"name": "demo", "type": "EXECUTABLE", "artifacts": [{"path": (output_dir / "demo.exe").as_posix()}]},
                    "plug.json": {"name": "plug", "type": "MODULE_LIBRARY", "artifacts": [{"path": (output_dir / "plugins" / "plug.dll").as_posix()}]},
                }
                for name, target in targets.items():
                    touch(reply / name, json.dumps(target))
                touch(reply / "codemodel.json", json.dumps({"configurations": [{
                    "name": "Release",
                    "projects": [{"name": "demo"}],
                    "targets": [{"jsonFile": name} for name in targets],
                }]}))
            else:
                touch(output_dir / "demo.exe")
                touch(output_dir / "plugins" / "plug.dll")

        _, result, _ = self.run_build(project, {"cmake.exe": run_cmake})
        self.assertNotIn("error", result)
        self.assertTrue((result["path"] / "demo.exe").is_file())
        self.assertTrue((result["path"] / "plugins" / "plug.dll").is_file())

    def test_qmake_package_in_build_root_leaves_out_intermediates_and_qml_copy(self):
        project = touch(self.root / "app" / "app.pro")
        touch(project.parent / "Main.qml", "import QtQuick\nItem {}\n")

        def qmake(cwd, args):
            touch(cwd / "Makefile", "DESTDIR_TARGET = app.exe\n")
            touch(cwd / ".qt-settings-", "release\n")

        def make(cwd, args):
            for name in ("app.exe", "object_script.app", "app_resource.rc", ".qt-intermediates/obj/main.o"):
                touch(cwd / name)

        builder, result, _ = self.run_build(project, {"qmake.exe": qmake, "mingw32-make.exe": make})
        self.assertNotIn("error", result)
        self.assertEqual(sorted(path.name for path in result["path"].iterdir()), ["app.exe"])
        deploy = builder.process.started[-1][1]
        self.assertEqual(Path(deploy[deploy.index("--qmldir") + 1]).name, compiler_module.QML_SOURCE_COPY)

    def test_cmake_project_with_several_unnamed_executables_is_rejected(self):
        project = touch(self.root / "demo" / "CMakeLists.txt")
        _, hooks = self.cmake_hooks("other")
        _, result, _ = self.run_build(project, hooks)
        self.assertIn("several executables", result["error"])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_msvc_make_prefers_jom_and_falls_back_to_nmake(self):
        builder = QtCompiler()
        builder.qt_bin = str(self.qt_bin)
        builder.compiler = Toolchain(self.root / "VS", "x86_64", kind=MSVC, msvc="19.38")
        builder.jobs = 4
        nmake = touch(self.root / "msvc bin" / "nmake.exe")
        builder._environment = {"Path": str(nmake.parent)}
        self.assertEqual(builder._make_command(), (nmake, ["/NOLOGO"]))
        jom = touch(self.root / "Qt" / "Tools" / "QtCreator" / "bin" / "jom" / "jom.exe")
        self.assertEqual(builder._make_command(), (jom, ["-j4", "/NOLOGO"]))

    def test_error_lines(self):
        for line in (
            "main.cpp:3:5: error: 'x' was not declared",
            "main.cpp(3): error C2065: 'x': undeclared identifier",
            "LINK : fatal error LNK1104: cannot open file",
            "mingw32-make: *** [Makefile:45: all] Error 2",
            "FAILED: CMakeFiles/app.dir/main.cpp.obj",
            "CMake Error at CMakeLists.txt:3 (find_package):",
        ):
            self.assertTrue(is_error_line(line), line)
        for line in ("g++ -Werror=format -c main.cpp", "Compiled with 0 errors", "error.cpp", "Copied errors.txt"):
            self.assertFalse(is_error_line(line), line)

    def test_handle_output_splits_lines_and_keeps_stderr_warnings_plain(self):
        builder = QtCompiler()
        emitted = []
        builder.output_signal.connect(emitted.append)
        builder.handle_output("main.cpp:5:9: warning: unused variable 'x'\nmain.cpp:6:9: error: undeclared\n", True)
        self.assertEqual(
            emitted,
            ["main.cpp:5:9: warning: unused variable 'x'\n", "main.cpp:6:9: error: undeclared\n"],
        )
        self.assertFalse(is_error_line(emitted[0]))
        self.assertTrue(is_error_line(emitted[1]))

    def test_start_process_allows_unnormalized_parent_directory(self):
        builder = QtCompiler()
        builder._build_root = self.output
        recorded = []
        builder.process = FakeProcess({})
        unnormalized = self.qt_bin / ".." / "bin" / "qmake.exe"
        builder._start_process(Stage.QMAKE, unnormalized, [])
        self.assertEqual(builder.process.started, [("qmake.exe", [])])

    def test_qmake_normalizes_relative_destdir_and_keeps_intermediates_out_of_package(self):
        project = touch(self.root / "app" / "app.pro")

        def qmake_relative(cwd, args):
            touch(cwd / "Makefile", "DESTDIR_TARGET = .qt-deployment/app.exe\n")
            touch(cwd / ".qt-settings-", "release\n")
            touch(cwd / ".qt-destdir-", "../custom\n")

        hooks = {
            "qmake.exe": qmake_relative,
            "mingw32-make.exe": lambda cwd, args: touch(cwd / ".qt-deployment" / "app.exe"),
        }
        _, result, _ = self.run_build(project, hooks)
        self.assertNotIn("error", result)
        self.assertEqual(result["path"], self.output / "custom")

        def qmake_escape(cwd, args):
            touch(cwd / "Makefile", "DESTDIR_TARGET = .qt-deployment/app.exe\n")
            touch(cwd / ".qt-settings-", "release\n")
            touch(cwd / ".qt-destdir-", "../../outside\n")

        _, escaped, _ = self.run_build(project, {"qmake.exe": qmake_escape})
        self.assertIn("outside the output directory", escaped["error"])

        # When build_dir == _build_root and auto-clean is off, intermediates stay in .qt-build-* and are not published.
        builder = QtCompiler()
        builder.process = FakeProcess({
            "qmake.exe": lambda cwd, args: (
                touch(cwd / "Makefile", "DESTDIR_TARGET = app.exe\n"),
                touch(cwd / ".qt-settings-", "release\n"),
            ),
            "mingw32-make.exe": lambda cwd, args: (
                touch(cwd / "app.exe"),
                touch(cwd / ".qt-intermediates" / "obj" / "main.o"),
            ),
        })
        published = {}
        builder.finished_signal.connect(lambda path: published.update(path=path))
        builder.error_signal.connect(lambda error: published.update(error=error))
        builder.compile_qt_project(
            str(project), str(self.qt_bin), Toolchain(self.mingw_bin, "x86_64"),
            str(self.output), [], True, False, 2,
        )
        while builder._stage is not None:
            builder.process_finished(0)
        self.assertNotIn("error", published)
        self.assertEqual(sorted(p.name for p in published["path"].iterdir()), ["app.exe"])
        preserved = [p for p in self.output.iterdir() if p.name.startswith(".qt-build-")]
        self.assertEqual(len(preserved), 1)
        self.assertTrue((preserved[0] / "Makefile").is_file())
        self.assertTrue((preserved[0] / ".qt-intermediates" / "obj" / "main.o").is_file())

    def test_cmake_rejects_escaped_paths_prefers_root_project_and_handles_module_plugins(self):
        project = touch(self.root / "demo" / "CMakeLists.txt")
        touch(self.root / "Qt" / "Tools" / "CMake_64" / "bin" / "cmake.exe")
        touch(self.root / "Qt" / "Tools" / "Ninja" / "ninja.exe")

        # Escaping artifact path must be rejected.
        def bad_cmake(cwd, args):
            if args[0] == "-S":
                bdir = Path(args[args.index("-B") + 1])
                reply = bdir / ".cmake" / "api" / "v1" / "reply"
                touch(reply / "index-1.json", json.dumps({"objects": [{"kind": "codemodel", "jsonFile": "codemodel.json"}]}))
                touch(reply / "demo.json", json.dumps({"name": "demo", "type": "EXECUTABLE", "artifacts": [{"path": "../outside.exe"}]}))
                touch(reply / "codemodel.json", json.dumps({"configurations": [{"name": "Release", "projects": [{"name": "demo"}], "targets": [{"jsonFile": "demo.json"}]}]}))

        _, bad_result, _ = self.run_build(project, {"cmake.exe": bad_cmake})
        self.assertIn("Invalid path inside the package", bad_result["error"])

        # Subproject with parentIndex should not shadow root project, and module plugins should be built/staged properly.
        build = {}

        def rich_cmake(cwd, args):
            if args[0] == "-S":
                build["dir"] = Path(args[args.index("-B") + 1])
                reply = build["dir"] / ".cmake" / "api" / "v1" / "reply"
                touch(reply / "index-1.json", json.dumps({"objects": [{"kind": "codemodel", "jsonFile": "codemodel.json"}]}))
                targets = {
                    "demo.json": {"name": "demo", "type": "EXECUTABLE", "artifacts": [{"path": "demo.exe"}]},
                    "sub.json": {"name": "sub", "type": "EXECUTABLE", "artifacts": [{"path": "sub/sub.exe"}]},
                    "plug.json": {"name": "myplug", "type": "MODULE_LIBRARY", "artifacts": [{"path": "plugins/myplug.dll"}]},
                    "qmlplug.json": {"name": "myqmlplug", "type": "MODULE_LIBRARY", "artifacts": [{"path": "MyQml/myqmlplug.dll"}]},
                }
                for name, target in targets.items():
                    touch(reply / name, json.dumps(target))
                touch(reply / "codemodel.json", json.dumps({"configurations": [{
                    "name": "Release",
                    "projects": [{"name": "demo"}, {"name": "sub", "parentIndex": 0}],
                    "targets": [{"jsonFile": name} for name in targets],
                }]}))
            else:
                touch(build["dir"] / "demo.exe")
                touch(build["dir"] / "plugins" / "myplug.dll")
                touch(build["dir"] / "MyQml" / "myqmlplug.dll")
                touch(build["dir"] / "MyQml" / "qmldir", "module MyQml\nplugin myqmlplug\n")

        builder, result, _ = self.run_build(project, {"cmake.exe": rich_cmake}, jobs=2)
        self.assertNotIn("error", result)
        configure, build_cmd, _ = builder.process.started
        self.assertIn(f"-DCMAKE_C_COMPILER={(self.mingw_bin / 'gcc.exe').as_posix()}", configure[1])
        self.assertIn("--target", build_cmd[1])
        self.assertIn("myplug", build_cmd[1])
        self.assertIn("myqmlplug", build_cmd[1])
        self.assertTrue((result["path"] / "demo.exe").is_file())
        self.assertTrue((result["path"] / "plugins" / "myplug.dll").is_file())
        self.assertFalse((result["path"] / "myqmlplug.dll").exists())

    def test_msvc_build_runs_vcvars_as_preparation_stage_and_isolates_mspdbsrv(self):
        project = touch(self.root / "app" / "app.pro")
        vcvars = touch(self.root / "VS" / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat")
        nmake = touch(self.root / "msvc_bin" / "nmake.exe")
        touch(self.root / "msvc_bin" / "cl.exe")
        compiler = Toolchain(vcvars.parent, "x86_64", kind=MSVC, msvc="19.38", toolset="14.38", host="x86_64")

        builder = QtCompiler()
        emitted = []
        builder.output_signal.connect(emitted.append)

        class MsvcFakeProcess:
            running = False

            def __init__(self):
                self.started = []

            def start(self, program, args, cwd, env, *, encoding="utf-8", cmdline=None):
                self.started.append((Path(program).name.casefold(), list(args), cmdline))
                name = Path(program).name.casefold()
                if name == "cmd.exe":
                    builder.handle_output(f"Path={nmake.parent}\r\nSECRET_TOKEN=hidden\r\n", False)
                elif name == "qmake.exe":
                    touch(Path(cwd) / "Makefile", "DESTDIR_TARGET = release/app.exe\n")
                    touch(Path(cwd) / ".qt-settings-", "release\n")
                elif name == "nmake.exe":
                    touch(Path(cwd) / "release" / "app.exe")

        builder.process = MsvcFakeProcess()
        result = {}
        builder.finished_signal.connect(lambda path: result.update(path=path))
        builder.error_signal.connect(lambda error: result.update(error=error))
        builder.compile_qt_project(str(project), str(self.qt_bin), compiler, str(self.output), [], True, True, 2)
        stages = []
        while builder._stage is not None:
            stages.append(builder._stage)
            builder.process_finished(0)
        self.assertNotIn("error", result)
        self.assertEqual(stages, [Stage.PREPARATION, Stage.QMAKE, Stage.BUILD, Stage.DEPLOY])
        self.assertIn("-vcvars_ver=14.38", builder.process.started[0][2])
        self.assertIn("_MSPDBSRV_ENDPOINT_", builder._environment)
        self.assertFalse(any("SECRET_TOKEN" in line for line in emitted))

    def test_build_process_line_buffers_drains_all_chunks_and_terminates_lingering_helper(self):
        proc = BuildProcess(self.app)
        emitted = []
        proc.output.connect(lambda text, is_err: emitted.append((text, is_err)))

        # Multi-chunk drain + line buffering
        chunks = [b"hel", b"lo\nwor", b"ld"]
        def fake_peek(pipe, size):
            return (b"", len(chunks[0]) if chunks else 0, 0)
        def fake_read(pipe, size):
            return (0, chunks.pop(0))

        from codecs import getincrementaldecoder
        proc._streams = [[object(), getincrementaldecoder("utf-8")("replace"), False, ""]]
        with (
            patch.object(build_process.win32pipe, "PeekNamedPipe", side_effect=fake_peek),
            patch.object(build_process.win32file, "ReadFile", side_effect=fake_read),
        ):
            proc._forward_output(final=False)
            self.assertEqual(emitted, [("hello\n", False)])
            proc._forward_output(final=True)
            self.assertEqual(emitted, [("hello\n", False), ("world", False)])

        # Lingering helper (e.g. mspdbsrv.exe) after exit_code == 0 gets terminated after grace period
        proc._handles = object()
        proc._process = object()
        proc._job = object()
        proc._streams = []
        proc._stop_requested = False
        proc._terminating = False
        proc._exited_at = None
        times = iter([10.0, 10.5, 11.2])
        with (
            patch.object(build_process.win32event, "WaitForSingleObject", return_value=build_process.win32event.WAIT_OBJECT_0),
            patch.object(build_process.win32process, "GetExitCodeProcess", return_value=0),
            patch.object(build_process.win32job, "QueryInformationJobObject", return_value={"ActiveProcesses": 1}),
            patch.object(build_process.time, "monotonic", side_effect=times),
        ):
            self.assertIsNone(proc._completed_exit_code())
            self.assertFalse(proc._stop_requested)
            self.assertIsNone(proc._completed_exit_code())
            self.assertTrue(proc._stop_requested)


class MsvcToolchainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_msvc_kit_reads_the_compiler_version(self):
        kit = self.root / "6.5.3" / "msvc2019_64"
        touch(kit / "mkspecs" / "qconfig.pri", "QT_ARCH = x86_64\nQT_VERSION = 6.5.3\nQT_MSVC_MAJOR_VERSION = 19\nQT_MSVC_MINOR_VERSION = 29\n")
        toolchain = qt_toolchain(kit / "bin")
        self.assertEqual((toolchain.kind, toolchain.arch, toolchain.msvc, toolchain.version), (MSVC, "x86_64", "19.29", "6.5.3"))

    def test_visual_studio_compilers_pair_with_compatible_kits(self):
        installation = self.root / "2022" / "BuildTools"
        touch(installation / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat")
        for toolset, targets in (("14.38.33130", ("x64", "x86")), ("14.29.30133", ("x64",))):
            for target in targets:
                touch(installation / "VC" / "Tools" / "MSVC" / toolset / "bin" / "Hostx64" / target / "cl.exe")
        with patch("toolchains.discovery.host_architecture", return_value="x86_64"):
            compilers = msvc_toolchains([(installation, "Visual Studio Build Tools 2022")])
            self.assertEqual(vcvars_argument("x86_64"), "x64")
            self.assertEqual(vcvars_argument("x86"), "x64_x86")
            self.assertEqual(vcvars_argument("arm64"), "x64_arm64")
        self.assertEqual(sorted((compiler.arch, compiler.msvc) for compiler in compilers), [("x86", "19.38"), ("x86_64", "19.38")])
        self.assertEqual(compilers[0].label, "Visual Studio Build Tools 2022")

        kit = Toolchain(Path("Qt/6.5.3/msvc2019_64/bin"), "x86_64", kind=MSVC, msvc="19.29")
        older = Toolchain(Path("VS2017"), "x86_64", kind=MSVC, msvc="19.16")
        mingw = Toolchain(Path("mingw/bin"), "x86_64", "11.2")
        chosen = matching_compiler(kit, [older, mingw, *compilers])
        self.assertEqual((chosen.arch, chosen.msvc), ("x86_64", "19.38"))
        self.assertIsNone(matching_compiler(kit, [older, mingw]))
        self.assertFalse(Toolchain(Path("Qt/mingw_64/bin"), "x86_64", "11.2").matches(chosen))

    def test_msvc_environment_comes_from_vcvarsall(self):
        vcvars = touch(self.root / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat")
        cl = touch(self.root / "MSVC" / "bin" / "cl.exe")
        compiler = Toolchain(vcvars.parent, "x86_64", kind=MSVC, msvc="19.38")
        output = f"Path={cl.parent}\r\nINCLUDE=C:\\MSVC\\include\r\n".encode()
        completed = subprocess.CompletedProcess("cmd", 0, output, b"")
        with (
            patch("toolchains.discovery.host_architecture", return_value="x86_64"),
            patch("toolchains.discovery.subprocess.run", return_value=completed) as run,
        ):
            environment = msvc_environment(compiler, {"Path": "C:\\Windows"})
        self.assertEqual(environment, {"Path": str(cl.parent), "INCLUDE": "C:\\MSVC\\include"})
        self.assertIn(f'"{vcvars}" x64', run.call_args.args[0])
        missing = subprocess.CompletedProcess("cmd", 0, f"Path={self.root / 'missing'}\r\n".encode(), b"")
        with (
            patch("toolchains.discovery.subprocess.run", return_value=missing),
            self.assertRaisesRegex(RuntimeError, "MSVC environment"),
        ):
            msvc_environment(compiler, {})

    def test_build_tools_ignore_the_current_directory_and_relative_path_entries(self):
        planted = touch(self.root / "working" / "cmake.exe")
        installed = touch(self.root / "tools" / "cmake.exe")
        previous = os.getcwd()
        os.chdir(planted.parent)
        self.addCleanup(os.chdir, previous)
        qt_bin = self.root / "Qt" / "6.5.3" / "mingw_64" / "bin"
        self.assertIsNone(find_build_tool("cmake.exe", qt_bin, ("CMake_64/bin",), f".{os.pathsep}working"))
        self.assertEqual(
            find_build_tool("cmake.exe", qt_bin, ("CMake_64/bin",), f".{os.pathsep}{installed.parent}"),
            installed,
        )

    def test_toolchain_cache_keeps_only_existing_installations(self):
        kit_bin = self.root / "Qt" / "mingw_64" / "bin"
        for name in ("qmake.exe", "windeployqt.exe"):
            touch(kit_bin / name)
        mingw_bin = self.root / "mingw" / "bin"
        for name in ("g++.exe", "mingw32-make.exe"):
            touch(mingw_bin / name)
        vcvars = touch(self.root / "VS" / "vcvarsall.bat")
        kits = {"Qt 6.5.3 (mingw_64)": Toolchain(kit_bin, "x86_64", "11.2", "6.5.3")}
        compilers = [
            Toolchain(mingw_bin, "x86_64", "11.2"),
            Toolchain(vcvars.parent, "x86_64", kind=MSVC, msvc="19.38", name="Visual Studio 2022"),
            Toolchain(self.root / "missing", "x86", "8.1"),
        ]
        data = json.loads(json.dumps(toolchains_to_data(kits, compilers)))
        self.assertEqual(toolchains_from_data(data), (kits, compilers[:2]))
        self.assertEqual(toolchains_from_data({"kits": {"x": {"bad": 1}}, "compilers": [1]}), ({}, []))
        self.assertEqual(toolchains_from_data({"kits": ["not-a-dict"], "compilers": 42}), ({}, []))
        self.assertEqual(
            toolchains_from_data({
                "kits": {"bad": {"directory": "", "arch": "x86_64"}},
                "compilers": [
                    {"directory": str(vcvars.parent), "arch": "mips", "kind": MSVC, "msvc": "19.38"},
                    {"directory": "relative/path", "arch": "x86_64"},
                    {"directory": str(mingw_bin), "arch": 123},
                ],
            }),
            ({}, []),
        )

    def test_msvc_environment_blocks_cmd_injection_and_uses_system_cmd(self):
        unsafe = touch(self.root / "bad%TEMP%" / "vcvarsall.bat")
        with self.assertRaisesRegex(ValueError, "Unsafe characters"):
            msvc_environment_command(Toolchain(unsafe.parent, "x86_64", kind=MSVC, msvc="19.38"))

        safe = touch(self.root / "VS 2022" / "vcvarsall.bat")
        cmd, command, vcvars = msvc_environment_command(
            Toolchain(safe.parent, "x86", kind=MSVC, msvc="19.38", toolset="14.38", host="x86_64")
        )
        self.assertEqual(cmd.name.casefold(), "cmd.exe")
        self.assertTrue(cmd.is_absolute())
        self.assertIn(f'call "{vcvars}" x64_x86 -vcvars_ver=14.38', command)
        # A failed setup is repeated with its messages sent to stderr for the build log.
        self.assertTrue(command.endswith(f'|| call "{vcvars}" x64_x86 -vcvars_ver=14.38 1>&2"'))

    def test_msvc_toolchains_falls_back_to_emulated_host_on_arm64(self):
        installation = self.root / "2022" / "Community"
        touch(installation / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat")
        touch(installation / "VC" / "Tools" / "MSVC" / "14.38.33130" / "bin" / "Hostx64" / "x64" / "cl.exe")
        with patch("toolchains.discovery.host_architecture", return_value="arm64"):
            compilers = msvc_toolchains([(installation, "VS 2022")])
        self.assertEqual(len(compilers), 1)
        self.assertEqual((compilers[0].arch, compilers[0].host, compilers[0].toolset), ("x86_64", "x86_64", "14.38"))
        self.assertEqual(vcvars_argument(compilers[0].arch, compilers[0].host), "x64")


class JobsPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parallel_jobs_are_saved_and_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            ini = str(Path(folder) / "settings.ini")
            preferences = AppPreferences(ROOT / "resource", QSettings(ini, QSettings.Format.IniFormat), QLocale("en"))
            self.assertEqual((preferences.jobs, preferences.build_jobs), (AUTO_JOBS, CPU_COUNT))
            self.assertEqual(JOB_CHOICES[:2], (AUTO_JOBS, 1))
            self.assertTrue(all(jobs <= CPU_COUNT for jobs in JOB_CHOICES))
            preferences.select_jobs(2)
            preferences.select_jobs(3)
            reopened = AppPreferences(ROOT / "resource", QSettings(ini, QSettings.Format.IniFormat), QLocale("en"))
            self.assertEqual((reopened.jobs, reopened.build_jobs), (2, 2))
            settings = QSettings(ini, QSettings.Format.IniFormat)
            for invalid in ("many", "3"):
                settings.setValue("build/jobs", invalid)
                settings.sync()
                self.assertEqual(AppPreferences(ROOT / "resource", settings, QLocale("en")).jobs, AUTO_JOBS)


if __name__ == "__main__":
    unittest.main()
