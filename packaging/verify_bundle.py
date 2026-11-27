# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise the distributed ctypes, QtNetwork and TLS binaries with no development PATH."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
from urllib.parse import quote


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    windows = Path(os.environ["SystemRoot"])
    os.environ["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    dll_directories = [os.add_dll_directory(str(bundle))]
    if (bundle / "PyQt6" / "Qt6" / "bin").is_dir():
        dll_directories.append(os.add_dll_directory(str(bundle / "PyQt6" / "Qt6" / "bin")))

    # Load the extension from the distribution explicitly, rather than its host-Python copy.
    sys.modules.pop("_ctypes", None)
    spec = importlib.util.spec_from_file_location("_ctypes", bundle / "_ctypes.pyd")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ctypes"] = module
    spec.loader.exec_module(module)
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleFileNameW.argtypes = (wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetModuleFileNameW.restype = wintypes.DWORD

    def dll_path(name: str) -> Path:
        dll = ctypes.WinDLL(name)
        buffer = ctypes.create_unicode_buffer(32768)
        if not kernel.GetModuleFileNameW(dll._handle, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buffer.value).resolve()

    if (bundle / "ffi.dll").is_file():
        assert dll_path("ffi.dll").is_relative_to(bundle), "ffi.dll came from the developer environment"
    assert Path(module.__file__).resolve().is_relative_to(bundle)
    print("PASS: distributed _ctypes and ffi.dll load with system-only PATH", flush=True)

    # Check the shipped XML backend instead of trusting the build interpreter.
    sys.modules.pop("pyexpat", None)
    expat_spec = importlib.util.spec_from_file_location("pyexpat", bundle / "pyexpat.pyd")
    expat = importlib.util.module_from_spec(expat_spec)
    sys.modules["pyexpat"] = expat
    expat_spec.loader.exec_module(expat)
    assert expat.version_info >= (2, 7, 2), expat.EXPAT_VERSION
    assert dll_path("libexpat.dll").is_relative_to(bundle), "XML backend came from the developer environment"
    print("PASS: distributed XML backend", expat.EXPAT_VERSION, dll_path("libexpat.dll"), flush=True)

    # Nuitka embeds PyQt6's Python package initializer; use a namespace here to load its actual .pyd files.
    pyqt = types.ModuleType("PyQt6")
    pyqt.__path__ = [str(bundle / "PyQt6")]
    pyqt.__file__ = str(bundle / "PyQt6" / "__init__.py")
    sys.modules["PyQt6"] = pyqt
    from PyQt6 import QtCore, QtNetwork, QtWidgets
    assert Path(QtCore.__file__).resolve().is_relative_to(bundle)
    assert Path(QtNetwork.__file__).resolve().is_relative_to(bundle)
    assert dll_path("Qt6Core.dll").is_relative_to(bundle)
    assert dll_path("Qt6Network.dll").is_relative_to(bundle)
    print("Distributed Qt versions:", json.dumps({
        "runtime": QtCore.qVersion(), "pyqt_compile_time": QtCore.QT_VERSION_STR,
        "pyqt": QtCore.PYQT_VERSION_STR,
    }), flush=True)

    plugins = bundle / "PyQt6" / "Qt6" / "plugins"
    os.environ["QT_QPA_PLATFORM"] = "windows"
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")
    os.environ["QT_PLUGIN_PATH"] = str(plugins)
    app = QtWidgets.QApplication([])
    app.setLibraryPaths([str(plugins)])
    assert dll_path("qwindows.dll").is_relative_to(bundle)
    print("PASS: distributed Windows platform plugin loads with system-only PATH", flush=True)

    translator = QtCore.QTranslator()
    translation_path = Path(QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.TranslationsPath)).resolve()
    assert translation_path == bundle / "PyQt6" / "Qt6" / "translations", translation_path
    assert translator.load(QtCore.QLocale("zh_CN"), "qtbase", "_", str(translation_path))
    app.installTranslator(translator)
    line_edit = QtWidgets.QLineEdit()
    menu = line_edit.createStandardContextMenu()
    assert any("复制" in action.text() for action in menu.actions())
    print("PASS: distributed Qt standard Simplified Chinese context-menu translation", flush=True)

    assert "schannel" in QtNetwork.QSslSocket.availableBackends(), QtNetwork.QSslSocket.availableBackends()
    assert QtNetwork.QSslSocket.supportsSsl()
    default_backend = QtNetwork.QSslSocket.activeBackend()
    assert default_backend, "Qt did not select a default TLS backend"
    print("Default Qt TLS backend:", default_backend, "supportsSsl=True", flush=True)
    manager = QtNetwork.QNetworkAccessManager()

    def request(url: str, *, limit: int | None = None, maximum: int = 4 * 1024 * 1024, collect: bool = True) -> tuple[bytes, dict]:
        request = QtNetwork.QNetworkRequest(QtCore.QUrl(url))
        request.setAttribute(
            QtNetwork.QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QtNetwork.QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setRawHeader(b"User-Agent", b"QtPackageTool-package-verification")
        request.setTransferTimeout(30000)
        reply = manager.get(request)
        loop = QtCore.QEventLoop()
        chunks = bytearray()
        aborted = False
        captured_ssl = None
        received = 0
        digest = hashlib.sha256()

        def append(data: bytes) -> None:
            nonlocal received
            received += len(data)
            digest.update(data)
            if collect:
                chunks.extend(data)

        def receive() -> None:
            nonlocal aborted, captured_ssl
            append(bytes(reply.readAll()))
            if received > maximum or (limit is not None and received >= limit):
                captured_ssl = reply.sslConfiguration()
                aborted = True
                reply.abort()

        reply.readyRead.connect(receive)
        reply.finished.connect(loop.quit)
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        timer.start(45000)
        loop.exec()
        timer.stop()
        append(bytes(reply.readAll()))
        status = reply.attribute(QtNetwork.QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        ssl = captured_ssl if captured_ssl is not None else reply.sslConfiguration()
        result = {"status": status, "bytes": received, "host": reply.url().host(), "protocol": ssl.sessionProtocol().name, "sha256": digest.hexdigest()}
        assert received <= maximum, "The transport probe exceeded its byte budget"
        assert status == 200, (result, reply.errorString())
        assert reply.error() == QtNetwork.QNetworkReply.NetworkError.NoError or aborted, reply.errorString()
        assert ssl.peerCertificateChain(), "The HTTPS response had no certificate chain"
        reply.deleteLater()
        return bytes(chunks), result

    contents, api = request("https://api.github.com/repos/NCFXZ/QtPackageTool/releases?per_page=1")
    releases = json.loads(contents)
    assert isinstance(releases, list)
    print("PASS: distributed QtNetwork default-backend GitHub HTTPS release-list transport", json.dumps(api), flush=True)
    assets = releases[0].get("assets", []) if releases else []
    required_names = ("QtPackageTool-Setup-x64.exe", "QtPackageTool-files.json")
    matching = {name: [asset for asset in assets if asset.get("name") == name] for name in required_names}
    if all(len(matching[name]) == 1 for name in required_names):
        for name in required_names:
            asset = matching[name][0]
            size = asset.get("size")
            digest = asset.get("digest", "")
            assert type(size) is int and 0 < size <= 2 ** 31 - 1, asset
            assert isinstance(digest, str) and digest.startswith("sha256:") and len(digest) == 71, asset
            _, download = request(asset["browser_download_url"], maximum=size, collect=False)
            assert download["bytes"] == size, download
            assert download["sha256"] == digest[7:].lower(), download
            print("PASS: distributed QtNetwork full release asset download and GitHub SHA-256", name, json.dumps(download), flush=True)
    else:
        print("NO_PUBLIC_RELEASE_ASSET: full installer and file manifest are not both public; source archive checks transport only", flush=True)
        contents, repository = request("https://api.github.com/repos/NCFXZ/QtPackageTool")
        assert json.loads(contents).get("full_name") == "NCFXZ/QtPackageTool"
        print("PASS: independent repository API HTTPS transport", json.dumps(repository), flush=True)
        contents, tags_result = request("https://api.github.com/repos/NCFXZ/QtPackageTool/tags?per_page=1")
        tags = json.loads(contents)
        assert isinstance(tags, list) and tags, "No existing source tag is available for the archive probe"
        tag = tags[0]["name"]
        archive_url = "https://github.com/NCFXZ/QtPackageTool/archive/refs/tags/" + quote(tag, safe="") + ".zip"
        archive, download = request(archive_url, limit=65536)
        assert archive.startswith(b"PK"), "The source archive did not return ZIP bytes"
        assert download["host"] == "codeload.github.com", download
        print("PASS: GitHub to codeload HTTPS source-tag archive transport", tag, json.dumps(download), flush=True)


if __name__ == "__main__":
    main()
