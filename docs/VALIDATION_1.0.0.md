# 1.0.0 验收记录

日期：2026-11-27。此记录区分源码回归、合成安装场景、真实安装版及隔离 Windows 测试；发布附件必须与最终构建输入及 SHA-256 一致。

## 本轮修复

- 安装与回滚前检查新增受管路径的用户文件、同名目录、父级文件和重解析点冲突。遇到冲突安全停止；不会继续用全量覆盖冲突文件。
- 全量升级只清理旧清单中的过时受管文件，保留未知插件和用户文件；严格拒绝损坏、重复或非法的旧清单。
- 更新与偏好存储对深层 JSON、异常字段和超长数字作受控失败处理；更新服务防止同步取消或重入后的旧操作继续发起请求。
- 持续构建输出采用有界读取和长行分段，保留最终输出，并允许事件循环及时处理停止及进程树取消。
- 修复配置页销毁时的无效表格回调；选择变化统一驱动移除按钮，清空和删除行后仍正确禁用。
- 应用进程及快捷方式统一 AppUserModelID；使用新 1024 px PNG 和九尺寸 ICO。概要空值使用独立的亮暗莫兰蒂灰棕色。
- 停止按钮使用空心圆角方框，边线比例与开始按钮的空心播放图标一致；随亮暗主题切换并保持中心透明。
- 清理编译器中重复且无调用的辅助常量导入，以及依赖校验模块未使用的私有解析器导入。保留实际使用和文档约定的接口。

## 源码、安全和界面

| 检查 | 当前结果 | 本地证据 |
| --- | --- | --- |
| 完整回归与语法 | 两套 Python 环境各 139 项通过，包含日志导出和空心停止图标专项 | `logs/final-ui-tests-outline-release-final.txt`、`logs/final-ui-tests-outline-release-final-venv.txt` |
| 依赖与原生运行时 | 17 个锁定包通过 pip check；pip-audit 无已知漏洞、无跳过；分发构建使用 Python 3.11.18 / Expat 2.10.0 | `logs/acceptance-1.0.0/security/pip-audit-current.json`、`native-smoke.json` |
| 有界对抗测试 | 600 次 Release 元数据变异、1420 次 PE 截断/变异、400 条部署路径，深层 JSON、异常偏好、同步取消、持续输出和真实 Job 子进程取消均通过 | `tests/test_security_boundaries.py` |
| 中英文与几何 | 182 条译文完整且占位符一致；5 种缩放、2 种语言、2 种主题、3 个尺寸、4 个页面，共 240 个布局与 140 个更新状态通过 | `logs/acceptance-1.0.0/ui-current/summary.json` |
| 界面稳定性 | 36 次窗口创建/销毁、72 轮语言/主题组合、288 次页面切换、504 次更新状态变更；销毁后控件数为零，检查期间内存增长有界 | `logs/acceptance-1.0.0/ui-stability.json` |
| 日志操作 | 纯文本、UTF-8 与 Windows 换行导出、写入失败处理、复制内容及清空后的按钮状态通过 | `tests/test_page_layout.py` |
| 颜色可读性 | 浅色 `#78695c` 在实际预览卡片上为 5.15:1；深色 `#b8aa9c` 为 6.25:1，主题切换及非空值恢复默认文字色正确 | `logs/morandi-summary/verification.json` |

旧 Python 的 Expat 2.6.0 不作为本轮分发运行时。Python 官方说明 XML 的安全性依赖所链接的 Expat；小 XML 导致过量动态分配的问题在 Expat 2.7.2 修复。本轮升级到实际加载的 2.10.0，并已复验最终包内 DLL。[Python XML 文档](https://docs.python.org/3.11/library/xml.html#xml-vulnerabilities)、[Expat 修复记录](https://github.com/libexpat/libexpat/blob/R_2_7_2/expat/Changes)。

## 安装与升级

| 检查 | 当前结果 |
| --- | --- |
| Inno 文件所有权保护 | 本轮实际 12 个场景全部通过，包含 Inno 6/7、重装、文件/目录/父路径冲突、非法清单和真实 junction；设置与注册已恢复 |
| 合成应用的真实 helper 安装 | 本轮 10 个场景全部通过：增量变化/新增/删除、正常退出等待、坏 ZIP/尺寸/路径/重复、部分安装失败、锁文件、全量接管和最终恢复 |
| 真实 Qt Package Tool 1.0.0 → 1.0.1 | 最终冻结的真实 Nuitka 产物 7 个场景全部通过，设置和快捷方式已恢复；1.0.1 仅用于本地验收，不发布 |
| 最终全量安装包 | 安装、正常退出等待、覆盖升级、仅删除旧清单受管插件、保留用户文件/设置/日志、更新后重启及卸载全部通过 |
| 最终安装版功能与图标 | 已通过实际 GUI 构建、产物启动、设置恢复、旧包保护、取消/重试/正常关闭；窗口、任务栏与两个快捷方式图标已核实 |
| 真实 MSVC | Qt 6.5.3、MSVC 19.44.35230.0、SDK 10.0.26100.0，qmake/CMake × Release/Debug 四组均构建成功，产物以系统 PATH 启动并退出 0 |
| 干净 Windows | 另一个全新默认安全策略实例中，无 Python/Qt，安装、原生 GUI、Qt 产物、正常关闭和卸载均通过 |

helper 合成测试首次重跑暴露测试工具读取正在写入的诊断 JSON 的竞争；已让验证脚本等待写入完成，完整重跑通过。产品下载与安装校验保持严格。证据：`logs/delta-verification-9n87nvm9/summary.json`、`logs/full-ownership-0lxot9dn/summary.json`、`logs/acceptance-update-safety-22yvx57g/summary.json`。

真实 Qt 跨版本场景包括正常增量、损坏 ZIP 回退全量、部分增量失败回退、增量及全量均失败后的恢复、真实 EXE 文件锁，以及普通/损坏增量两种用户文件冲突安全停止。通过生产 `launch_delta_update` 交接到实际安装的 helper，旧 GUI 正常关闭，新 GUI 的关于页通过 UIA 核实版本，最终核对受管文件 SHA-256、用户文件、设置、注册和卸载。新增的目标文件仅为本地验收标记。网络使用预置并按生产哈希校验的本地缓存，不代表公开过 1.0.1。证据：`logs/acceptance-qt-update-installed-xfolnrf4/summary.json`。首次验收脚本误按系统编码读取带中文路径的 UTF-8 诊断文件，现已修正；完整七项重跑通过。

最终全量包另通过实际 Inno 安装/覆盖/卸载验收，证据：`logs/installer-verification-k2k0c89c/summary.json`。该检查与原生 GUI 构建检查均使用同一最终安装包摘要。

最终安装版 SHA-256：`922f2b3676e59d446f2f48a2afe08a7c1b5976763023ef3b0f7dd5cad96c1ed5`。同一附件分两次完成原生界面验收：首次脚本误用被省略的概要标签判断项目路径，后改为读取配置页真实输入值，剩余交互重跑通过，清理均正常。证据：`logs/installed-acceptance-final-k0nxoc_4/matrix.json`、`quick-matrix.json`、`logs/installed-acceptance-final-vwp4lunm/summary.json`。

| 实际构建路径 | 套件 | 模式 | 产物启动 |
| --- | --- | --- | --- |
| qmake Widgets | Qt 6.5.3 / MinGW 11.2 x64 | Release | 系统 PATH，退出 0 |
| qmake Widgets | Qt 5.15.2 / MinGW 8.1 x64 | Debug | 系统 PATH，退出 0 |
| CMake Widgets | Qt 5.15.2 / MinGW 8.1 x64 | Release | 系统 PATH，退出 0 |
| CMake Widgets | Qt 6.5.3 / MinGW 11.2 x64 | Debug | 系统 PATH，退出 0 |
| qmake Quick/QML | Qt 6.5.3 / MinGW 11.2 x64 | Release | 系统 PATH，退出 0 |
| CMake Quick/QML | Qt 5.15.2 / MinGW 8.1 x64 | Debug | 系统 PATH，退出 0 |
| qmake Widgets | Qt 6.5.3 / MSVC 19.44 x64 | Release | 系统 PATH，退出 0 |
| qmake Widgets | Qt 6.5.3 / MSVC 19.44 x64 | Debug | 系统 PATH，退出 0 |
| CMake Widgets | Qt 6.5.3 / MSVC 19.44 x64 | Release | 系统 PATH，退出 0 |
| CMake Widgets | Qt 6.5.3 / MSVC 19.44 x64 | Debug | 系统 PATH，退出 0 |

Widgets 场景包含显式 Qt Network 和外部文件部署。Quick 场景验证 QRC 内 QML 与运行依赖。实际英文暗色、中文亮色的四页原生截图已保存；空心停止图标在实际编译阶段验证。

最终 bundle 检查包含包内 `_ctypes`、Expat、Qt Windows 平台插件、中文 Qt 菜单和 Schannel TLS 1.3。源码归档传输只证明发布前网络链路，公开附件仍须在发布后完整下载核对。Defender 对最终安装包及目录的扫描未检出威胁；该结果不表示不存在未知漏洞。

MSVC 使用 CMake 3.30.5，结果见 `logs/acceptance-1.0.0/security/sandbox-results-msvc/result.json` 及 `environment.json`。隔离桌面继承 175% 缩放，验收工具修正了坐标转换，并为 UIA 的瞬时读取失败添加有界重试；qmake Debug 已完成的实际产物独立启动复验后继续其余场景，最终四组均通过并正常卸载。

用户明确授权仅在一次性 MSVC 工具环境中临时处理 Sandbox 的 MSI 延迟。该实例已销毁，宿主安全设置未修改。另启实例的 Smart App Control 状态仍为默认值 `2`，没有 Python、Qt 或开发工具；同一最终包安装、GUI 响应、Qt 产物启动和卸载均成功。GitHub Latest 在发布前返回预期的 404，表示无公开正式版，不能代替发布后附件复验。证据：`logs/acceptance-1.0.0/security/sandbox-results-final/guest-result.json`。

## 重现入口

在 Windows x64 的已安装依赖环境中，按需单独运行，安装验证之间须串行，避免争用同一 AppId：

```powershell
python -B -m unittest discover -s tests -v
python -B packaging/verify_ui.py
python -B packaging/verify_file_ownership.py
python -B packaging/verify_delta.py --iscc PATH_TO_ISCC
python -B packaging/verify_real_update.py --safety-only
python -B packaging/build_windows.py
python -B packaging/verify_bundle.py dist/main.dist
python -B packaging/verify_installer.py dist/QtPackageTool-Setup-x64.exe
```

真实跨版本验证另需由冻结源码构建的、未发布的真实 1.0.1 bundle，使用 `verify_real_update.py` 的 baseline/target 参数。故障注入只替换测试载荷；源测试与模拟传输不能替代实际二进制安装结果。

## 分发结果与边界

2026-11-27 20:49 UTC，全部发布前验收通过；`logs/acceptance-1.0.0/final-gate.json` 将上述结果绑定到最终安装包和构建输入。源输入摘要：`d06c20e63002aa715c69f4321c31172fefebcb49eeb5cc749064f109865a2994`。

| 附件 | 字节数 | SHA-256 |
| --- | --- | --- |
| `QtPackageTool-Setup-x64.exe` | 62,657,528 | `922f2b3676e59d446f2f48a2afe08a7c1b5976763023ef3b0f7dd5cad96c1ed5` |
| `QtPackageTool-files.json` | 23,902 | `90d4f682a6e2e18567e1429df94400e4cb2d1d8fb18d261e14a8af89439f3dd4` |

发布后完整下载、实际客户端更新检查与下载的复验结果将登记在主分支的本记录。未发布的本地 1.0.1 测试载荷和重复构建产物已清理，保留摘要、样例源文件、截图和日志。

- 目标为 Windows 10/11 x64；当前实际宿主和 Sandbox 为 Windows 11，未声称验证所有 Windows 版本、硬件或 ARM。
- 本轮漏洞查询覆盖锁定的 Python 包和识别出的原生 Expat 风险；格式变异和压力检查有明确边界。
- 安装包的 Authenticode 状态为 `NotSigned`；GitHub 附件及客户端更新使用 SHA-256 核对，未将哈希校验描述为代码签名。
- Qt、MinGW、MSVC 的版本和实际构建组合在最终结果中逐一记录。旧 Beta 的便携 ZIP 不作为更新来源；1.0.0 建立首个稳定基线。

隔离 MSVC 工具环境遇到 MSI 每项签名策略等待约两分钟的问题，与微软 Sandbox 仓库中维护者确认的[问题 #68](https://github.com/microsoft/Windows-Sandbox/issues/68)匹配。准备环境的记录与最终软件在默认策略干净实例中的结果分开保存，不把环境准备过程作为产品功能已通过的证据。
