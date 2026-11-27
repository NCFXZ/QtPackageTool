# Qt Package Tool

[简体中文](#简体中文) | [English](#english)

## 简体中文

一款 Windows 平台的 Qt C++ 项目编译与打包工具。选择项目和 Qt 套件后，自动完成编译，并将程序和所需的 Qt 运行依赖整理到输出目录。

### 主要功能

- 支持 qmake（`.pro`）和 CMake（`CMakeLists.txt`）项目。
- 自动识别 Qt 套件，匹配 MinGW 或 MSVC 编译工具。
- 支持 Release、Debug 构建，收集 Qt 运行库、插件和 QML 依赖。
- 支持添加额外文件、文件夹和 Qt 组件。
- 实时显示构建进度和日志，支持停止打包、复制及保存日志。
- 自动保存项目配置，支持中英文和浅深色主题。
- 支持应用内更新，优先下载有变化的文件，失败时改用完整安装包。

### 下载与安装

从 [GitHub Releases](https://github.com/NCFXZ/QtPackageTool/releases/latest) 下载 `QtPackageTool-Setup-x64.exe`。

支持 Windows 10/11 x64，安装版无需另行安装 Python。

编译项目还需准备：

- Qt 开发套件及匹配的 MinGW 或 MSVC 编译工具。
- CMake 项目所需的 CMake。
- 使用 MSVC 时，安装 Visual Studio 或 Build Tools 的 C++ 开发组件及 Windows SDK。

### 使用方法

1. 打开「项目配置」，选择 Qt 套件。
2. 选择项目文件、输出目录及 Release 或 Debug 模式。
3. 根据需要添加 Qt 依赖和外部文件。
4. 进入「项目打包」，点击「开始打包」。
5. 完成后点击「打开打包目录」查看结果。

打包结果保存在独立文件夹中，避免覆盖已有结果。项目额外使用的第三方 DLL、配置文件等，可通过「外部文件」添加。

### 应用更新

程序支持自动检查和下载更新，也可在「关于」页手动检查。下载完成后，点击安装按钮完成更新并重新启动程序。

自动下载可在「设置」中关闭。安装更新前，请先完成或停止当前打包任务。

### 从源码运行

需要 Windows x64 和 64 位 Python 3.11。在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src/main.py
```

源码运行支持检查和下载更新；应用内安装更新需使用安装版。

### 构建本工具的安装包

安装 Inno Setup 6 或 7，并完成上述依赖安装后执行：

```powershell
.\.venv\Scripts\python.exe packaging/build_windows.py
```

生成的安装包位于 `dist/QtPackageTool-Setup-x64.exe`。

### 许可证

本项目采用 [GPLv3 许可证](https://github.com/NCFXZ/QtPackageTool/blob/main/LICENSE)。

## English

A Windows tool for building and packaging Qt C++ projects. Select a project and a Qt kit to compile the application and collect it with the required Qt runtime dependencies in the output folder.

### Features

- Supports qmake (`.pro`) and CMake (`CMakeLists.txt`) projects.
- Automatically detects Qt kits and matches MinGW or MSVC build tools.
- Supports Release and Debug builds, collecting Qt runtime libraries, plugins, and QML dependencies.
- Includes additional files, folders, and selected Qt components.
- Shows live build progress and logs, with controls to stop packaging, copy logs, and save logs.
- Saves project settings automatically and supports English, Simplified Chinese, and light and dark themes.
- Supports in-app updates that prefer changed files and fall back to the full installer if an update fails.

### Download and Installation

Download `QtPackageTool-Setup-x64.exe` from [GitHub Releases](https://github.com/NCFXZ/QtPackageTool/releases/latest).

Supports Windows 10/11 x64. The installed app does not require a separate Python installation.

To build projects, you also need:

- A Qt development kit and matching MinGW or MSVC build tools.
- CMake for CMake projects.
- For MSVC, the C++ development tools and Windows SDK from Visual Studio or Build Tools.

### Usage

1. Open **Project Setup** and select a Qt kit.
2. Select the project file, output folder, and Release or Debug mode.
3. Add Qt dependencies and external files as needed.
4. Open **Packaging** and click **Start Packaging**.
5. When finished, click **Open Package Folder** to view the result.

Each package is saved in a separate folder to avoid overwriting existing results. Add any extra third-party DLLs, configuration files, or other project files through **External Files**.

### App Updates

The app can automatically check for and download updates. You can also check manually in **About**. Once the download finishes, click the install button to update and restart the app.

Automatic downloads can be disabled in **Settings**. Finish or stop any active packaging task before installing an update.

### Run from Source

Requires Windows x64 and 64-bit Python 3.11. Run these commands from the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src/main.py
```

Update checks and downloads are available when running from source. In-app update installation requires the installed version.

### Build the Installer

Install Inno Setup 6 or 7 and the dependencies above, then run:

```powershell
.\.venv\Scripts\python.exe packaging/build_windows.py
```

The installer is generated at `dist/QtPackageTool-Setup-x64.exe`.

### License

This project is licensed under [GPLv3](https://github.com/NCFXZ/QtPackageTool/blob/main/LICENSE).
