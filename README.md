# Qt Package Tool
Qt Package Tool is a Windows-only packaging assistant for Qt C++ projects, providing a simple interface to build and package qmake (.pro) and CMake (CMakeLists.txt) projects with MinGW or MSVC Qt kits.

Project structure, local setup, and current progress are documented in [AGENTS.md](AGENTS.md) and [PROGRESS.md](PROGRESS.md).

## Install and update

Download `QtPackageTool-Setup-x64.exe` from [GitHub Releases](https://github.com/NCFXZ/QtPackageTool/releases/latest). The installer supports Windows 10/11 x64 and installs for the current user.

The installed app checks for a new stable release at startup and downloads it automatically. You can turn off automatic downloads in Settings or check manually from About.

Updates prefer a smaller file-level delta for the installed version. A missing or unsuitable delta uses the full installer; failed delta verification or installation automatically falls back to the full package. Version 1.0.0 establishes the first baseline, so its first installation uses the full installer.

The version row in About uses one compact pale-blue button: a rotating arc while checking, a check mark when up to date, a progress ring while downloading, and **Install {version}** with an icon when the installer is verified. Click the arc or progress ring to cancel. Text actions include icons, and the GitHub and update buttons share a width suited to the display language. The button supports keyboard activation without an inner focus border. Failed checks, downloads and updates show an X icon on the same button; its tooltip provides details, and clicking it retries the check. There is no separate status line below the version row.

Choose **Install {version}** to save the current settings, close the app, install the update and restart. Finish the active packaging task before installing an update. Project settings and application preferences are preserved.

Source runs support manual update checks and downloads; installation is available from the installed app.

## Build the Windows installer

Use 64-bit Python 3.11, the pinned dependencies, and Inno Setup 6 or 7:

```powershell
python -m pip install -r requirements.txt
python packaging/build_windows.py
```

The build produces a Nuitka standalone application, `dist/QtPackageTool-Setup-x64.exe`, and `dist/QtPackageTool-files.json`. The shared version and release metadata are defined in `src/app/metadata.py`. Publish every asset listed in `dist/release-manifest.json` under a stable `vX.Y.Z` GitHub release; update checks require GitHub's SHA-256 asset digests.

For a later release, pass `--base-manifest PATH` with the previous stable release's `QtPackageTool-files.json`. The build compares managed files and adds a matching delta ZIP when it is smaller than the full installer. The build uses the Windows .NET Framework C# compiler for the independent update helper; no additional Python package is required.

## License
Python QFluentWidgets is licensed under GNU General Public License v3.0 (GPLv3) for non-commercial project. See the [LICENSE](https://github.com/NCFXZ/QtPackageTool/blob/main/LICENSE) file for the full license text.

This project uses PyQt6, which is licensed under the GPLv3.

## Author
Copyright (c) 2025 NCFXZ
