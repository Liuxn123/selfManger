# selfManger · v1.0.1

> 当前源码调整：已移除 Catalog 资源库服务与页面；项目资料关联改由知识库索引元数据提供，旧资源文件不会自动迁移。Hermes、通用 Agent 管理与备份、会话读取和应用内 Codex 任务执行也已移除；项目 Agent 文档及 Skills 保留，原登记、资料和旧备份不会自动删除。下文旧功能描述属于历史说明，不能作为当前入口。

[下载 Windows 64 位免安装包（约 60 MB）](https://github.com/Liuxn123/selfManger/releases/download/v1.0.1/selfManger-1.0.1-win32-AMD64.zip) · [1.0.1 发布说明与校验文件](https://github.com/Liuxn123/selfManger/releases/tag/v1.0.1)

完整解压后运行 `selfManger/selfManger.exe`，无需安装 Python；请保留整个程序目录。当前提供 Windows 包，具体测试范围与已知限制见发布说明。

旧版升级请先阅读 [一次性数据迁移](docs/RENAME_MIGRATION.md)。应用只使用新路径和名称；便携包默认独立空数据，使用已迁移本机数据时需指定数据目录。

**每天开始工作的个人工作台，用来管理今日计划、项目、知识资源和数据备份。**

本仓库维护selfManger的桌面软件源码、使用说明、测试与打包流程。项目资料、知识库、凭据、运行数据库和备份保存在用户本地，不随源码仓库上传。当前版本号为 1.0.1；版本改动见 [更新记录](docs/RELEASE_NOTES.md)，安装包验证范围见 [验证记录](docs/VALIDATION.md)。

核心工作区为今日工作台、计划、项目、工具与环境、知识库、数据安全和设置。个人行动、计划、复盘和项目任务统一以 SQLite 为权威数据源；项目规则、Agent 协作状态、交接与知识正文继续使用本地 Markdown。新建项目采用四步引导，支持模板、开发规范、项目内 Skills 副本和知识资料关联。

个人行动、计划、复盘和项目任务都以 SQLite 为应用内真源。项目 `agent/TASKS.md` 只记录 Agent 开发协作事项，不会导入应用任务；旧 `TASKS-AI.md` / `TASKS-MANUAL.md` 可预览并一次性迁入，冲突时保留数据库记录，源文件始终原样保留。旧个人每日与计划 Markdown 只作一次性导入来源，导入后不再跟踪外部修改或写回 Markdown。当前源码行为与 v1.0.1 发布包应分别验收。

打开程序，先看今天做什么，再进入正在进行的项目并管理相关资料。项目规则、Agent 状态、交接与知识正文使用本地文件，可以由 Obsidian 编辑；工作台、计划和项目任务共用 SQLite。备份与恢复集中在“数据安全”，“计划”入口集中查看跨日安排与中长期目标。

项目页采用左侧项目卡片、右侧项目工作区：先看当前阶段和下一步，再看目标、进展、阶段总结、待办、风险与最近记录。应用任务列表和统计来自 SQLite；STATUS、Agent 协作 TASKS、HANDOFF 等原文及 Obsidian 入口仍可查看和编辑；目录、归档及索引维护集中在“更多操作”。

项目归档 / 重新启用通过 STATUS 生命周期管理，资料目录保持原位；归档时隐藏登记的 Obsidian 项目入口，重新启用后恢复。项目身份和相对目录由 `projects.json` 登记，生命周期由 STATUS 保存，应用任务与动态分别来自 SQLite 和 HANDOFF。

## 模块入口

| 入口 | 解决什么问题 |
| --- | --- |
| 工作台（默认首页） | 所选日行动、周期打卡、继续项目、到期与复盘提醒，以及六类计划和模块入口 |
| 计划 | 快速新建、每日计划、长期计划、周期计划、周月复盘、项目计划；与工作台共用 SQLite 行动数据 |
| 项目 | 当前阶段与下一步、STATUS / TASKS / HANDOFF、阶段总结、笔记、Git 状态与操作、归档与重新启用 |
| 工具与环境 | 在 SQLite 登记常用本地程序和 HTTP(S) 网站入口；只读查看本机 Python、Git、SSH、GPG 等环境探测结果 |
| 知识库 | 统一添加文件、链接、笔记和整个目录，按专题整理并阅读资料 |
| 设置 | 配置 Markdown 与统一备份目录、备份保留数量、统一备份密码和浅色/深色主题；备份目录可指向本机网盘同步目录 |
| 数据安全 | 保护数据库、项目、任意文件夹、脚本输出和 Obsidian 资料；归档类备份压缩加密并统一存放，支持校验、历史版本与恢复 |

工具与环境只保存程序路径或网站地址；不会安装软件、执行命令或保存网址内的账号口令与令牌。旧 Catalog 入口已退出；已有旧 Markdown 资源可在知识库中按支持的格式一次性迁入，原件保留。知识库的“添加资料”默认复制文件和整个目录，也可选择引用原文件；不运行资料中的命令。阶段总结提供人工编辑和可复制 Prompt，不引入 AI 调度框架。

## 开始使用

Windows 便携包完整解压后运行 `selfManger/selfManger.exe`。保留整个目录；不要只复制 exe。macOS / Linux 源码运行和对应平台构建仍需分别验证；本项目现有实际验证以 Windows 为准。

```powershell
# 在 selfManger 项目根目录执行
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
$env:PYTHONPATH = (Join-Path $PWD "src")
.\.venv\Scripts\python.exe -m selfManger
```

macOS / Linux：激活虚拟环境后 `python -m pip install -e . && python -m selfManger`。Python 需要 3.11+。各平台分别打包，不能拿 Windows exe 在其他系统运行。

`.venv` 是本机环境，不随项目迁移。当前本机环境的基础解释器来自 Codex 运行时缓存；换电脑或缓存失效时，用自己安装的 Python 3.11+ 按上述步骤重新创建虚拟环境并安装依赖，不要复制旧 `.venv`。运行依赖由 `pyproject.toml` 声明，打包依赖见 `requirements-build.txt`。

Windows 源码启动可双击 `start.cmd`，默认进入“工作台”。源码目录可放置不纳入 Git 的本机配置 `local-config.json` 指定数据位置；`--data-dir` 和 `SELFMANGER_DATA_DIR` 可分别通过命令行或环境变量覆盖本机配置。启用 `require_existing_database: true` 时，若指定数据库不存在，应用会报错而不创建空数据库。没有本机配置时使用平台用户数据目录；便携 exe 默认将数据放在程序目录旁的 `data`，不读取源码本机配置；可通过 `--startup-page plan` 进入“计划”。

第一次打开：

1. 在“项目”选择已有规范工作区（含 `agent/projects.json`），或初始化一个空文件夹。
2. 在“工作台”查看所选日行动，点击“记录行动”或今日行动卡片中的“添加行动”新建每日行动。日期框和左右箭头切换日期，“回到今天”返回今天。六类计划入口进入对应模块；行动状态与周期打卡保存在 SQLite。旧个人 Markdown 首次读取时导入一次，后续编辑请在应用内进行。
3. 进入项目查看阶段与下一步；新建时依次填写信息、调整开发规范、选择技能与资料并确认创建。
4. 在“知识库”添加文件、链接、笔记或整个目录，选择保存位置后整理阅读。
5. 需要保护资料时进入“数据安全”，管理备份登记、口令与恢复。

## Markdown 与 Obsidian

项目 Agent 协作文档、项目笔记及知识库正文/附件仍使用文件；个人行动、计划、复盘和项目任务由 SQLite 管理。旧个人资料目录可用于导入历史 Markdown，导入后不会继续将文件变化同步为应用任务。有工作区时，文字资料默认放在 `myself/selfManger/`；没有工作区时放在管家数据目录的 `工作台/`。设置可选择另一个真实文件夹；选择 Obsidian 知识库内的目录即可共用。

```text
selfManger/
  每日/2026-10-05.md
  资源/<唯一编号>.md
```

上面的每日文件和 Markdown 资源示例表示旧格式导入来源，不是当前应用任务的保存格式。项目 STATUS / TASKS / HANDOFF 和项目笔记仍是可编辑文档；其中 `agent/TASKS.md` 仅记录 Agent 开发协作事项。阶段总结追加到 HANDOFF，不创建重复的 SUMMARY / PROGRESS / REFLECTION 文件。

项目文档和知识文件可由 Obsidian 打开；具体刷新策略因页面而异。项目文档保存会比较原文件版本，若检测到 Obsidian 外部修改则拒绝覆盖。管家不创建或修改 `.obsidian` 配置。

## 数据兼容与安全

- 数据安全内置“数据库与 Markdown 整体备份”：包含数据库（含设置和知识索引）、个人计划目录和当前工作区登记项目的 Markdown，以及项目登记快照。不包含项目非 Markdown 附件、知识库文件、系统凭据及指定系统/依赖目录；遇到范围内的其他联接会停止。采集时阻止其他数据库写入，并检查 Markdown 前后哈希；请暂停 Obsidian、AI 等外部编辑器。备份产生临时明文工作副本，正常完成后清理；异常退出可能需要检查系统临时目录。
- 整体备份内含数据库架构、应用版本、时间和文件哈希清单。恢复前校验并预览，只恢复到新目录或空目录；提取时原数据库隔离为 `manager.sqlite3.recovered`，默认数据库阻止直接启动。核对提取结果后，可用“准备可用数据目录…”生成新数据目录和项目/个人 Markdown，并重映射已知路径；原隔离副本保持不变。请独立保管口令，丢失无法解密；实际恢复演练通过后才把该版本视为已演练。
- `projects.json` 负责项目身份和相对目录登记，STATUS 保存项目生命周期，Agent 协作文档作为文件维护，应用任务和个人行动数据保存在 SQLite。
- 旧版升级曾使用 `workbench_schema=1` 配置标记；它不会批量改写项目或搬动个人文件。旧版 `work-summary.md` 首次读取或备份时一次性导入 SQLite，原文件保留；之后不再同步外部修改。
- 资源级 `.amb` 仍使用 schema 1 / AES-GCM 加密格式，用于已登记的本地文件夹、项目、脚本输出及 Obsidian 资料；只有明确启用的知识库 Git 管理才会纳入 Git 历史。创建归档时不生成明文中间 ZIP。Hermes、通用 Agent 与会话备份入口已移除；SQLite 整体备份是另一条独立流程。
- 恢复先预览、校验再确认；.amb 恢复只写入新目录或空目录。恢复演练使用临时目录，不覆盖原资料。
- Hermes、通用 Agent 备份与会话管理入口已移除；项目页可扫描仓库并提供 Git 状态、暂存、提交及远程操作。源码与 v1.0.1 发布包的功能范围分别验收。
- U 盘内的程序配置路径随便携目录移动；外部项目 / 知识库 / SSH 私钥仍需自行携带或重新选择。资料 Markdown 不会被当作应用数据库正文保存。

资料文件夹在 U 盘外时，移动程序本身不会携带它。可把资料文件夹登记为 Obsidian / 项目备份，或在关闭相关应用后复制整个资料文件夹。原应用的登录、运行环境与能否续聊，仍需在目标电脑验证。

## 验证与打包

```powershell
.venv\Scripts\python scripts/run_tests.py
.venv\Scripts\python scripts/build.py
.venv\Scripts\python scripts/smoke_package.py
```

测试用例涉及加密恢复、错误口令 / 篡改 / 路径边界、SQLite / JSONL 快照、迁移登记、便携路径、项目归档、Obsidian 联动、每日任务、资源编辑冲突、阶段总结与界面衔接。CI 配置了 Windows、Linux、macOS Intel / Apple Silicon 构建；配置存在不等于当前源码已在这些平台通过验证。近期源码和旧发布包的验证结论应分开读取。

逐模块运行器为每个模块创建独立进程，开始即输出进度，每 15 秒报告仍在运行；完整用例输出写入日志，较慢模块会转储线程栈。超时终止该模块后继续其余模块，失败或超时返回非零退出码。日志和逐模块更新的 `summary.json` 默认保存在新建的 `qa/tests-<时间>-<随机编号>/`：

```powershell
.venv\Scripts\python scripts/run_tests.py                 # 全部模块
.venv\Scripts\python scripts/run_tests.py knowledge ui    # 名称包含 knowledge 或 ui 的模块
.venv\Scripts\python scripts/run_tests.py --timeout 600
.venv\Scripts\python scripts/run_tests.py action_center --output qa/my-new-test-run
# 也可从源码仓库根目录独立运行单模块或单用例
.venv\Scripts\python -m unittest tests.test_repeat_options -v
.venv\Scripts\python -m unittest discover -s tests -t . -v
```

测试使用标准库 `unittest`，GUI 需要已安装的 PySide6，默认 `QT_QPA_PLATFORM=offscreen`。`SELFMANGER_TEST_ROOT` 可选：运行器默认设为本轮输出下的 `fixtures/`；直接运行时辅助函数回退系统临时目录。需要指定可写位置时，先设置 `$env:SELFMANGER_TEST_ROOT = (Join-Path $PWD 'qa/test-fixtures')` 并创建该目录。隔离目录继承父目录权限，仅写虚构资料，不修改系统 ACL。权限或符号链接能力仍受宿主约束，遇到失败先看完整日志；不能把环境失败当作业务通过。`--output` 必须是尚不存在的目录，避免覆盖上一轮证据。

## 文档

- [分模块使用说明](src/selfManger/assets/docs/USER_GUIDE.md)
- [恢复范围与限制](src/selfManger/assets/docs/RESTORE_SCOPE.md)
- [架构与数据关系](docs/ARCHITECTURE.md)
- [路线图](docs/ROADMAP.md)
- [版本说明](docs/RELEASE_NOTES.md)
- [性能检查](docs/PERFORMANCE.md)
- [本机验证记录](docs/VALIDATION.md)

不提供邮件、云盘或完整日历客户端，不开发完整 MCP Host、工作流引擎、多 Agent 自动编排、浏览器自动化或 Obsidian 替代品。扩展以实际日常使用需求为依据。
