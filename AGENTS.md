# Qt Package Tool 项目指南

## 项目背景

Qt Package Tool 是一个 Windows 桌面工具，用于编译和部署基于 qmake 的 `.pro` 或基于 CMake 的 `CMakeLists.txt` Qt C++ 项目，支持 MinGW 和 MSVC 套件。界面使用 Python、PyQt6 和 QFluentWidgets，通过 Qt 工具链生成可执行文件，并把运行所需的 Qt DLL、插件和用户指定的文件放入输出目录。项目采用 GPLv3，许可见 `LICENSE`。

## 根目录结构

| 路径 | 用途 |
| --- | --- |
| `src/main.py` | 启动入口，调用 `app.bootstrap.run()`。 |
| `src/app/` | 应用初始化、界面与构建协调、资源路径、偏好存储和基础翻译。 |
| `src/app/updates/` | 最新正式版信息、固定安装包校验、异步检查与流式下载。 |
| `src/build/` | 构建阶段协调、进程控制，以及 qmake、CMake、QML 搜索和部署路径辅助模块。 |
| `src/toolchains/` | Qt、MinGW 与 MSVC 的发现、基础模型、配对与 MSVC 环境。 |
| `src/deployment/` | Qt 依赖发现、运行依赖校验和 PE 文件解析。 |
| `src/UI/` | 页面、公共控件、样式和窗口通知。 |
| `resource/config/` | 随程序分发的 Qt 库与附属 QML 模块关联配置。 |
| `resource/i18n/` | 简体中文 Qt 翻译源文件和运行时词条文件。 |
| `resource/images/` | 窗口、关于页和托盘用的 PNG 图标，以及供 Windows 应用打包使用的 ICO 图标。 |
| `tests/` | 语言与偏好保存、图标唯一性、工具链发现和配对、MSVC 环境、工具链缓存、qmake 与 CMake 构建流程、依赖及 QML 发布校验、页面布局规则的回归测试。 |
| `packaging/` | Windows x64 Nuitka standalone、文件清单、Inno 全量/增量包与 DLL 分发配置。`updater/` 保存独立更新进程的 C# 源码。 |
| `docs/` | 版本验收记录，区分源码、合成安装、真实安装版和隔离 Windows 的测试结果。 |
| `logs/` | 源码运行时优先使用的日志目录，由 Git 忽略。发布版使用 `%LOCALAPPDATA%\QtPackageTool\logs`。 |
| `README.md`、`AGENTS.md`、`PROGRESS.md` | 项目简介、维护指南和当前进度。 |
| `requirements.txt`、`LICENSE`、`.gitignore` | Python 依赖版本、许可证和 Git 忽略规则。 |
| `.vscode/settings.json` | 本机 VS Code 的 Python 环境偏好，由 Git 忽略。 |

## 功能定位

| 功能 | 入口和职责 |
| --- | --- |
| 应用启动与界面联动 | `src/main.py` 调用 `src/app/bootstrap.py` 的 `run()` 初始化应用。`src/app/controller.py` 的 `QtPackage` 创建主窗口和编译器，绑定界面事件与编译信号，协调状态提示、通知和输出目录入口。表格行的增删和读取由设置页提供，`QtPackage` 只负责校验和协调。 |
| 运行配置、翻译与偏好 | `src/app/runtime.py` 统一源码与分发版的资源路径、日志配置和异常处理；`src/app/bootstrap.py` 负责初始化。`src/app/localization.py` 提供翻译与语言选择，`src/app/preferences.py` 保存偏好、项目会话、工具链缓存和窗口几何。上述状态与基础翻译接口不依赖页面模块。 |
| 应用更新 | `src/app/metadata.py` 统一版本、发布日期、仓库和产物名称。`src/app/updates/` 处理 GitHub 最新正式 Release、可选匹配增量、官方来源、异步下载、取消、超时和 SHA-256 校验，不执行安装。增量下载失败只回退全量一次，源码下载使用全量。`src/UI/ui_updates.py` 保持单按钮展示，`QtPackage` 协调偏好与构建门禁；`src/app/installer.py` 通过独立参数交接全量 Inno，或复制 helper 到下载目录后传入结构化计划。helper 等待应用退出，校验基线、备份受管文件、监督增量 Inno、复验目标，失败时恢复并尝试全量；启动入口在加载界面前检查更新互斥锁。 |
| 构建辅助与基础模型 | `src/build/qmake.py` 处理 qmake 参数和求值后的 Makefile，`src/build/cmake.py` 处理 CMake 参数和 File API，`src/build/qml.py` 发现、过滤和复制 QML 源码，`src/build/paths.py` 校验部署路径。`src/toolchains/models.py` 定义工具链与配对，`src/deployment/pe.py` 解析 PE 架构和导入表。这些模块不依赖 Qt 界面或编译协调器。 |
| Qt、MinGW 与 MSVC 检测 | `src/toolchains/discovery.py` 的 `discover_toolchains` 被动读取固定磁盘常用安装目录、环境变量、PATH 和 Qt Creator 登记路径，存在 MSVC 套件时再通过 `vswhere` 和 Visual Studio 安装目录查找各架构的 `cl.exe`。浏览入口支持安装根目录、版本目录、套件目录及 `bin`。`QtPackage` 启动时先显示上次自动扫描缓存的套件，再在后台重新扫描并保留当前选择。 |
| 工具链配对 | `src/toolchains/discovery.py` 读取 Qt 的 `qconfig.pri` 和 MinGW 安装布局，`src/toolchains/models.py` 按架构及可识别的 GCC 主次版本挑选 MinGW；MSVC 套件按架构挑选 `cl.exe` 版本不低于套件记录版本的 Visual Studio，优先较新版本。同等匹配时优先选择套件附近的工具。界面只显示可配对的 Qt 套件，构建前复核。MSVC 构建使用 `vcvarsall.bat` 生成的环境。构建使用所选套件的默认 spec，配对的工具优先于继承的 PATH。 |
| 项目、构建与资源设置 | `src/UI/ui_package_settings.py` 提供 `.pro` 或 `CMakeLists.txt`、输出目录、Release 或 Debug、自动清理、外部文件与 Qt 依赖搜索。项目文件可拖到页面上选择，文件和文件夹可拖入外部文件表格，空表格显示操作提示。`src/app/controller.py` 将选择转换为构建模式所需的实际来源、目标和类型，并通过 `AppPreferences` 在关闭和开始打包时保存项目配置，下次启动时跳过已失效的路径恢复，套件和依赖在扫描完成后恢复。 |
| Qt 依赖导入 | `src/deployment/qt_dependencies.py` 从当前套件的 `bin/`、`plugins/` 和 `qml/` 发现可选依赖。`resource/config/qt_dependencies.json` 将虚拟键盘等库关联到附属 QML 模块。显式选择的依赖也交由 `windeployqt` 收集并纳入发布校验。 |
| 编译与部署 | `src/build/compiler.py` 以 `Stage` 枚举推进构建。qmake 项目在本次专用目录运行 qmake 和 make，MinGW 用 `mingw32-make`，MSVC 优先用 jom、否则用 nmake，并复核选中的构建模式。CMake 项目用套件附近或 PATH 中的 CMake 配置和构建，有 Ninja 时优先使用，通过 CMake file API 找到要打包的可执行文件（多个时取与项目同名者），把它和项目生成的 DLL 复制到干净的包目录。并行任务数来自应用设置。自定义绝对 `DESTDIR` 重定向时，链接后命令中对应路径也指向本轮目录。资源全部部署后才发布到 `release/`、`debug/` 或选定输出目录内的自定义 `DESTDIR`。已占用的名称改用数字后缀。启用自动清理时删除本次中间文件，关闭时保留成功构建的中间文件。 |
| QML 搜索路径 | `src/build/qml.py` 扫描项目目录和当前构建模式下 `RESOURCES` 声明的 QRC，识别工程目录外的 QML 和脚本，跳过输出目录、旧包和构建目录，并把结果复制到本轮的 `.qt-qml-sources` 交给 windeployqt 扫描。qmake 项目部署采用求值后的 `QML_IMPORT_PATH`，CMake 项目把构建目录加入导入路径以找到项目自建的 QML 模块，环境变量 `QML_IMPORT_PATH` 和 `QML2_IMPORT_PATH` 也可补充搜索目录。相对搜索路径以项目文件所在目录为基准。 |
| 发布校验 | `src/deployment/runtime_dependencies.py` 按包内 `qt.conf` 或 Qt 6 优先使用的 `qt6.conf` 解析 QML 导入目录，由套件的 `qmlimportscanner` 检查必需模块。校验模块声明的必需原生插件，并在包目录和 Windows 系统目录中检查应用及插件的 PE 启动依赖和包内 DLL 的架构。源文件校验排除输出目录中的生成文件。 |
| 构建进程控制 | `src/build/process.py` 负责启动工具、读取输出和管理 Windows Job Object。`QtCompiler` 在工作线程中编译、复制和清理。停止或关闭窗口时终止本次进程树。状态长期无法确认时会保留中间文件并报错。 |
| 打包界面 | `src/UI/ui_package_project.py` 提供构建概要、进度、实时日志、开始、停止和打开产物目录的界面。进度区右侧以分割线隔开复制、保存和清空日志按钮。`src/app/controller.py` 处理开始与停止，缺少套件、项目或输出目录时弹出带「前往项目配置」按钮的提示，把 `QtCompiler` 的构建阶段和结果信号转换为界面状态。构建日志中的阶段、复制和失败信息随界面语言翻译，只有符合编译器、链接器、make、Ninja 或 CMake 错误格式的行标为橙色。百分比按已完成的阶段计算，成功发布后达到 100%。 |
| 主窗口与应用设置 | `src/UI/ui_main.py` 组装展开的侧栏和透明页面。侧栏隐藏菜单按钮，窗口最小尺寸为 1180×680 逻辑像素，默认高度 800 并受屏幕可用高度限制。页面标题与侧栏名称一致。关闭时保存窗口大小和位置，构建中及完成或失败后未查看时在「项目打包」导航项显示状态圆点。`src/UI/ui_preferences.py` 将独立的 `AppPreferences` 接入语言、主题和并行任务数控件，主题字符串到界面库枚举的转换留在界面层。`src/UI/i18n.py` 仅负责页面文案刷新，并保留原翻译函数的兼容导入。`src/UI/ui_notifications.py` 封装纯文本提示条、单实例托盘和任务栏提示。 |
| 关于页与公共样式 | `src/UI/ui_about.py` 用概览卡展示应用名称、版权和仓库入口，用设置式卡片展示版本、许可、第三方库和问题反馈。`src/UI/ui_layout_settings.py` 维护各页的居中限宽布局、卡片对齐和副标题颜色。`src/UI/ui_icons.py` 统一维护导航、卡片和操作图标及应用 Logo 路径。同名按钮使用相同图标，其他功能使用可区分的图标。 |

## 运行与验证

在 Windows 上，从仓库根目录使用 Python 3.11 安装依赖并启动应用。

```powershell
python -m pip install -r requirements.txt
python src/main.py
```

从仓库根目录运行回归测试。

```powershell
python -B -m unittest discover -s tests -v
```

界面文案改动时同步维护 `resource/i18n/zh_CN.ts`，并用 Qt Linguist 的工具生成 `.qm`。构建模式使用工具链原名 `Release` 和 `Debug`。卡片副标题只保留能补充操作信息的内容，末尾省略句号。

```powershell
lrelease resource/i18n/zh_CN.ts -qm resource/i18n/zh_CN.qm
```

验证部署时，选择可信的 `.pro` 或 CMake 项目和已配对的 Qt 套件，检查 Release 和 Debug 产物在系统 PATH 下的启动结果。通过复用输出目录和触发资源冲突验证旧包保护。含空格和中文的路径可从界面选择，实际编码支持取决于 Windows 系统代码页及工具链版本。Nuitka 分发目录需要包含 `resource/config/`、`resource/images/` 和 `resource/i18n/zh_CN.qm`，运行时日志由应用写入用户数据目录。

构建安装包使用 `python packaging/build_windows.py`，依赖 Windows x64 Python 3.11、锁定的 Nuitka 及 Inno Setup 6/7。安装包固定命名为 `QtPackageTool-Setup-x64.exe`，版本与发布信息只在 `src/app/metadata.py` 修改。安装器按当前用户安装，保持固定 AppId，更新时等待旧进程正常退出，不强制停止构建，不删除 QSettings 或用户日志。发布使用正式 `vX.Y.Z` 标签和 GitHub 提供的安装包 SHA-256 digest；验证完整安装包后再发布和清理旧 Release。

首个正式版分发全量包及 `QtPackageTool-files.json`，后续使用 `--base-manifest PATH` 对上一正式版生成文件级增量 ZIP，只有小于全量时才纳入发布。应上传 `dist/release-manifest.json` 列出的全部附件。增量 ZIP 固定含 `delta.json`、`files.json` 和 `installer.exe`；增量 Inno 与全量共享 AppId，登记新增文件并仅删除明确过时的受管文件，不清空整个插件目录。独立 C# helper 使用 Windows Framework 编译器；缺少 .NET Framework 4.8 时客户端直接选择全量。文件清单、下载包、helper 及构建输入摘要均纳入分发验证；使用 `packaging/verify_delta.py` 验证真实安装、回滚、全量接管与卸载行为。

## 代码维护原则

- 尽量让模块间解耦。界面负责展示和交互，`QtPackage` 负责协调，`QtCompiler` 通过信号报告编译状态。沿现有接口传递必要数据，避免循环依赖。
- 按职责将新增模块放入 `app/`、`build/`、`toolchains/`、`deployment/` 或 `UI/`，`src/` 根目录仅保留启动入口。基础模型、PE 解析和构建辅助模块不依赖页面或应用协调器。
- 遵循最小化改动原则。复用现有模块和接口，将变更限定在当前任务涉及的行为和路径。
- 代码保持精简、高效且易读。减少重复逻辑、无效状态和无效计算，清理前检查运行入口、动态引用和分发资源。

## 进度文档维护

- 工作推进时及时更新根目录的 `PROGRESS.md`。重要变更完成、验证结果出现或阻塞状态变化时，同步更新当前状态、关键结果和待完成事项。
- 阶段收尾时清理 `PROGRESS.md`。合并重复条目，移除过期计划和已结束的临时事项，已完成的工作保留简短结论，详细历史以 Git 和发布记录为准。
