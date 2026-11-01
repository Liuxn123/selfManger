# selfManger 架构记录

## 当前源码架构（2026-11-01）

本节描述 2026-11-01 集成候选源码；本文从“历史版本实现记录”起至文末均为历史快照，原文保留供追溯，可能描述已退役实现，不代表当前行为。当前源码的版本和发布包版本应分别核对。

- **个人业务数据：**行动、计划、复盘与项目任务由 SQLite 保存和查询。旧个人每日/计划 Markdown 与旧项目 `TASKS-AI.md`、`TASKS-MANUAL.md` 只在兼容入口中一次性导入，原文保留；没有持续双向同步。`LegacyDailyReader` 只供每日任务导入器读取；今日页不监听每日 Markdown，也没有旧文件勾选写回或跨日期 Agenda 扫描。项目 `agent/TASKS.md` 只承载 Agent 开发协作事项，不会导入应用任务。数据访问集中在 `storage.py`、`action_center.py` 和 `project_task_documents.py`。
- **项目登记：**`agent/projects.json` 保存项目身份、工作区相对目录和可选项目信息；项目生命周期从 STATUS frontmatter 读取。STATUS / TASKS / HANDOFF、项目笔记与附件继续保存在文件中。`ProjectWorkspace` 负责登记、安全路径、生命周期及项目详情所需文档/笔记/资料读取和局部错误归集；`project_git.py` 负责 Git 扫描、状态读取及命令执行，`ui/projects.py` 负责页面、对话框和交互协调。
- **知识库：**正文与附件保存在 Vault 文件系统。SQLite 保存可重建文件索引、元数据、库外引用和资源组；旧 sidecar、`.kref` 与资源组 JSON 仅一次性迁移，索引可由文件重建。`KnowledgeIndex` 校验索引身份与根目录边界，并提供有界文本预览和笔记读取；UI 不直接打开文本文件。Catalog 已退出，不再作为第二套活动资源库。 最近使用时间与编辑表单元数据通过单一 SQLite 原子事务保存；回收站永久清理按本地副本、外部引用和合集所有权清除相应登记，重扫不会复现已清理索引。
- **数据安全：**资源级 `.amb` 归档提供加密、校验和提取；整体备份包含数据库及其声明范围内的个人/项目 Markdown。整体提取会隔离数据库；“准备可用数据目录”在不改写提取副本的前提下校验清单和 SQLite，并把已知路径映射到新目录。知识库正文/附件、项目非 Markdown 附件和系统凭据不包含在整体备份中。
- **页面与服务：**`ui/` 负责展示、交互和异步状态协调；数据规则分别集中在 SQLite Store、行动/计划服务、项目工作区、项目初始化服务、知识索引、知识收集写入、工作笔记、备份与 Git 服务。`ApplicationService` 负责资源登记适配器校验和持久化，也统一承接设置、计划草稿、后台操作历史、资源配置导入导出、项目工作区路径及资源/任务/状态证据查询和备份健康读取；UI 只协调调用、呈现结果并刷新。知识库文本读取和项目规范导入由对应服务执行有界读取与内容校验；用户工作笔记保存在 SQLite，旧 work-summary.md 首次读取或备份时幂等导入一次并保留原件。测试默认使用隔离合成数据。Windows 客户端的启动、页面切换与核心流程需结合自动化测试和目标环境复核；自动化测试不能替代主观视觉体验。迁移和恢复验证只适用于其隔离样本，不代表完整用户 Vault。静态检索确认，`ui/` 未直接调用 Store 方法或执行 SQLite SQL，也未直接写入/删除/移动业务文件；仅读取内置帮助与恢复说明，路径属性访问和服务调用仍由页面协调。

## 历史版本实现记录

以下章节保留原有 v0.6–v0.8.3 设计与变更说明。部分内容记录了后来已移除的 Markdown 任务写入、Catalog 或 Agent 备份入口；不得将其当作当前能力。当前决策以上述“当前源码架构”为准。

> 2026-10-28：Hermes 适配器、通用 Agent 备份与记录、应用内 Codex 执行已移除；项目 Agent 文档与 Skills 保留。下方涉及已移除实现的旧版本说明为历史记录。旧资源登记和旧备份不自动删除。

## 历史快照：2026-10-29 数据边界（当时待核验）

项目应用任务、个人行动、计划与复盘均以 SQLite 为唯一可变数据源。每日 Markdown 旧语料只允许一次性导入；迁入后 Agenda 不再扫描日记任务。每日任务的优先级、完成状态、日期、单点时间标记和日程时间段映射到 SQLite；旧计划保留起止日期、目标进度、父计划关系、复盘范围/正文、重复规则和打卡日期。项目 TASKS-AI/TASKS-MANUAL 只作为历史迁移来源，应用预览并一次性迁入，冲突保留数据库版本；原文不改写，之后文件编辑不再导入。新项目不生成专用任务清单。项目内 agent/TASKS.md 仍用于 Agent 开发协作，不作为应用任务数据库来源。个人计划/复盘正文、行动备注、周期规则和打卡状态直接读写 SQLite。数据库中登记的旧个人 Markdown 文档仅在首次访问时迁入有变化且格式有效的内容；原件保留，迁移完成后不再扫描或更新。此历史快照记录当时的实现与待验证事项，不作为当前验证结果。导入与恢复应使用隔离副本并保留原件；单一合成样本或单个工作区的验证不能推广为完整 Vault 验收。

项目页创建、编辑、状态与删除操作通过 `ActionCenter` 维护项目身份、来源和版本检查，再由 SQLite Store 写入；项目任务清单仍由 SQLite 查询。`project_task_transactions.py` 仅包装原子事务，不读写任务文档，不维护文件基线、同步恢复日志或导出协议。历史任务表解析保留在 `project_task_documents.py` 的一次性迁移入口。个人行动与计划事务在 `action_center.py` 中维护；`personal_documents.py` 只读旧文件路径并执行一次性历史导入，不再提供个人 Markdown 写入或同步 API。`document_transactions.py` 只在启动时读取并处理旧版待完成恢复日志，避免遗留崩溃记录丢失；新业务写入不再生成此类日志。

知识库正文和附件仍以 Vault 文件保存。`knowledge_files` 是由文件状态重建的索引；`knowledge_metadata` 保存资源描述字段；`knowledge_references` 保存库外文件身份与其库内位置；`knowledge_bundles` 保存合集登记。扫描受原有目录深度、文件数、隐藏路径、凭据和符号链接过滤限制。旧资源 JSON sidecar、`.kref` 与资源组 JSON 在每个 Vault 首次迁移时导入一次，`knowledge_legacy_imports` 记录格式批次和逐文件结果；迁移器不删除或改写原件，批次完成后不再读取新增或修改的旧登记文件。SQLite 备份包含元数据与引用登记，Vault 文件继续由 Vault 自身管理。正文 frontmatter 仍只作为原件内容读取，不改写。

## 2026-10-28 源码补充：恢复隔离

数据库与 Markdown 备份在采集期间锁定 SQLite 写入并核对文件前后哈希；外部编辑仍需暂停，不承诺跨程序原子快照。整体恢复先校验并在暂存区逐文件核对，再将数据库隔离为 `manager.sqlite3.recovered`，默认文件是空的启动阻挡数据库。安全路径重映射尚未实现，不得直接改名启动；**这是 2026-10-28 的历史状态，已由 2026-10-30 的整体恢复准备功能取代，当前步骤见本文顶部和 `USER_GUIDE.md`。**真实副本和打包验收仍待完成。非 Markdown 项目附件、知识库正文和凭据不在该备份范围内。

## Agent 管理模块（暂缓）

用户要求暂不实现 Agent 管理，因此已移除侧栏入口、工作台状态卡/轮询及专用管理界面。数据安全页仍通过现有备份服务保护已登记 Agent 资料；备份适配器与资源登记数据保留。

Agent 管理及 Skills Manager 的应用内启动入口暂未提供。`agent_skills.py` 仍供项目初始化只读发现本机已有 Skills 使用。

v0.8.3 数据安全首页增加统一的类型 / 登记项选择器、常用操作栏和健康状态卡，复用 `ResourcePage` 的登记、详情、目录及诊断控件，并仍将备份 / 恢复交给原 `ApplicationService` 与 Adapter。备份历史、换电脑恢复、操作记录和保护概览仍是原有独立子页；未更改归档格式、口令策略或 SQLite schema。

v0.8.2 资源库页面重排为筛选 / 列表 / 详情三栏，筛选状态仍来自 `Catalog` 扫描的 Markdown frontmatter，不增加 SQLite 正文或索引字段；设置页以卡片展示资料目录、备份目录和密码状态，既有保存处理与数据路径保持不变。

v0.8.0 项目归档 / 重新启用通过带管理锁的 `ProjectWorkspace.set_archived` 修改 STATUS 状态，不移动项目目录、不改变 projects.json 路径或 Obsidian 联接。Frontmatter 的 `archived_from` 暂存归档前状态，重新启用时恢复；旧归档缺少该字段时回到 active。项目归类、索引和工作台过滤均读取 STATUS lifecycle，因此兼容仍位于旧 `archive/` 目录的编号项目。旧移动方法保留供兼容，不再由此 UI 流程调用。

v0.8.0 项目状态概览将卡片操作直连真实数据源：目标 / 进展 / 风险编辑原 `agent/STATUS.md`，下一步编辑原 `agent/TASKS.md`，总结与日志继续走 HANDOFF 追加流程；状态标题另提供完整 STATUS 编辑入口。写入仍使用既有 CAS 保存，未创建第二份正文。

v0.7.9 侧栏首页显示为“工作台”。TodayPage 首次展示时加载，之后不监听文件变化、不响应窗口激活自动刷新，也不做日期 / 文件轮询；手动刷新按钮重读资料。应用内任务写入仍通过原成功回调刷新页面。其他 Markdown 页面保留各自的监听策略。

v0.7.8 今日页移除优先级编辑、优先级统计和专注计时。编辑计划仍写入当前日期 Markdown，保存回调刷新今日任务及对应日期的 Todo；保留每日文件、原子保存和冲突保护。

v0.7.7 仅调整 TodayPage 左栏任务卡片顺序：先“今日要做”，后 Todo；数据与交互保持不变。

v0.7.6 TodayPage 首页只保留 Todo 与当日任务。Todo 复用 Agenda 对每日 Markdown 的只读索引，以任务所在日期作为截止日；新增 Todo 仍调用 Daily.add_entry 写入日期文件的“今日任务”区。双击待办会切换到对应日期，由原任务清单负责勾选和冲突保护。没有新增 SQLite 或 projects.json 字段。完整日程仍在二级日程总览和 Markdown 编辑器中管理。

项目表现层位于 `ui/project_widgets.py`：绘制项目卡片，基于已加载 Markdown 提取状态概览、真实复选框 / 表格任务、日期日志与阶段总结摘要。宽度决定三 / 二 / 一栏；外层滚动保护小窗口操作。完整原文保留在同一组标签，菜单复用原 ProjectPage 操作和禁用规则。解析跳过围栏代码块示例；概览限长，未知章节仍可读原文。没有新增数据库字段、文件格式或后台轮询，切换标签 / 卡片原文不重新读取。

今日视图的卡片与绘制代理位于 `ui/today_widgets.py`；列表采用统一委托，不为每行创建控件。`Daily.add_entry` 使用既有文件锁与原文比较追加任务 / 日程。读取时仍兼容旧 Markdown 中的优先级标记，但今日界面不再新增或突出显示该标记；不升级 SQLite 或备份格式。

产品入口：工作台 → 项目 → Agent / 资源，数据保护按需进入。保留原工程、服务与 Adapter，不重建项目。

## 现有核心

- `domain.py` 的 Resource 仍是备份 / Agent 登记模型，id 与 options 保持兼容。
- `application.py` 统一调用 Adapter、资源 / 路径锁、备份、校验、恢复与演练。
- `adapters/`：本地 Hermes、服务器 Hermes、项目、任意本地文件夹、用户脚本备份、Vault、通用 Agent。文件夹/脚本输出复用统一 `.amb` 归档、压缩加密和恢复服务；脚本输出由隔离临时目录收集。服务器代码仍通过严格 SSH 执行受限操作。
- `archives.py` / `snapshots.py` / `security.py`：原 .amb schema 1、AES-GCM、SQLite / JSONL 快照、凭据策略与路径防护。
- `project_workspaces.py`：projects.json schema 2，身份分配、管理锁、STATUS / TASKS / HANDOFF、笔记、索引；归档 / 重新启用优先通过 STATUS 生命周期字段处理。
- `portable.py`：配置中程序目录内的路径编码成 @portable，引导 U 盘移动后的解码；外部路径保持外部路径。

## 日常工作数据

`workbench.py` 提供 bounded Markdown IO、Daily、Catalog、frontmatter、阶段与关系字段、总结模板 / Prompt 接口、Obsidian URI。

| 对象 | 真源 | 程序保存什么 |
| --- | --- | --- |
| Daily | 每日/YYYY-MM-DD.md | 日期、日程与勾选任务直接在文件，不保存正文数据库副本 |
| Project | 原 projects.json + STATUS / TASKS / HANDOFF / 笔记 | STATUS 新增可选 phase、agents、resources；阶段总结追加 HANDOFF |
| Agent | 原 SQLite Resource 登记 + Adapter | 目录、入口、项目编号；备注使用资源库 Agent Markdown |
| Resource（资源库条目） | 资源/*.md 的 YAML frontmatter 与正文 | 唯一 id、类型、标签、说明、来源、路径、适用 Agent / 项目、备注、收藏和人工安装 / 配置 / 测试标记 |

资源库条目与原 `domain.Resource` 不混用：Catalog 使用独立 Markdown 索引模型，不改备份清单。手写文件无 id 时使用 file:相对路径身份；编辑后保持稳定。重复 id 与非法字段明确显示错误。

关系通过编号列表实现。Project STATUS 引用 Agent 登记 id、资源 id；资源的 agents 可引用登记 id 或类型，projects 引用项目编号；Agent options 可引用项目编号。无复杂关系表，无正文索引数据库。

SQLite 保留资源登记、程序设置、状态证据、任务结果和应用自有工作笔记。旧 `work-summary.md` 只在首次读取时有界导入一次，原件保留；后续工作笔记读写使用 `personal_work_notes`，整体数据库备份随 SQLite 一并保护。新增 workbench_schema=1 及可选 workbench_root 配置，最近使用仅保存时间与 id。使用 Store 既有路径编码。

## 写入与外部编辑

每日 / 资源文件限制 1 MB、资源最多 1000 个、只扫描资源目录这一层。拒绝越界、符号链接 / junction 与非普通文件。写入使用独占锁、原文比较、临时文件与原子替换；新文件独占创建。外部编辑冲突明确拒绝覆盖。锁不会自动删除其他任务的锁。

Windows 原子替换遇到短暂读句柄占用（5 / 32 / 33）时，后台最多等待 250 ms 重试；每次重新检查安全路径与原文，期间有外部修改仍拒绝覆盖。其他权限错误直接报告，不绕过文件权限。

项目仍走原 ProjectWorkspace 管理锁与 CAS 写入。阶段字段只修补管理字段，保留正文和未管理 frontmatter；总结走原 append_log。项目关联字段错误要求人工修复。

Qt 文件监听、短防抖、窗口重新激活与日期切换触发后台读取。路径监听按差异更新；今日可见时每两秒只读取今日文件，补足 macOS 原子保存的合并 / 缺失通知，不扫描项目。Daily / Catalog 读取不产生操作历史或正文持久化；Catalog 按文件 stat 复用索引。操作写入仍走 Worker、任务状态和 GUI 线程回调，正文结果不入日志。

## UI

v0.7.5 ProjectPage 在目录移动前暂停窗口拥有的 QFileSystemWatcher（包括隐藏 Today 的项目监听）。watch / update_watches 在暂停标记期间拒绝迟到读取回调注册路径；MainWindow 的 task_completed 信号在任务结果处理结束后统一释放暂停，成功后按新路径刷新，失败 / 取消同样恢复。后台业务移动仍由既有 Worker、资源锁与工作区锁执行。旧 L-归档只使用既有 create 初始化接续项目，历史目录保持原位；不增加登记字段。

移动回执兼容旧 prepared / completed，失败新增 failed_before_move（未 rename 且来源清单一致）或 needs_review，并记录 phase、error_type、error、winerror；不自动回滚已移动目录。

v0.7.4 复用 Daily 按日期文件作为唯一真源。Agenda 是只读跨日期索引，按 stat 缓存、限制文件 / 总大小 / 显示条数、逐文件报告错误；只在打开总览或文件事件 / 手动刷新时后台读取，不增加全局轮询。ui/agenda.py 提供总览二级弹窗，打开日期后仍使用 TodayPage 的原编辑 / 勾选与 CAS 写入。未完成任务不自动改日期。

TodayPage 区分系统今日与当前浏览日期，跨午夜只推进原本停留在今天的视图；后台返回必须匹配日期与当前资料路径，避免快速切换日期后旧响应显示为新日期。添加 / 编辑捕获目标日期、路径与原文，写入仍走原 Worker。新增任意日期选择不改变 Markdown / SQLite / projects.json / .amb schema。

项目详情逐份读取管理文档，错误作为可见状态返回，避免 TASKS 缺失造成 STATUS / HANDOFF 一并空白。文档错误控制依赖它的编辑操作。CatalogPage.set_scope 统一清除遗留筛选，新建资源沿用当前关联；资源备注写入同一 Markdown 后导航到结果。打开系统目录返回实际成功状态，失败不记录使用成功。源码启动脚本显式选用相邻便携安装的数据，不合并或自动迁移其他数据目录。


一级堆栈为六页，使用命名导航常量。`ui/workbench.py` 提供 TodayPage / CatalogPage；ProjectPage 扩展阶段概览、阶段总结、关联资料。

数据安全复用原 ResourcePage、备份历史 / 迁移、操作记录与保护概览。每日无备份主按钮。工作笔记由 ApplicationService 读写 SQLite；旧 `work-summary.md` 首次读取时导入一次并保留原文件，之后不再同步外部修改。

任务列表按需加载摘要，选中才读详情；项目文档标签复用已加载正文。

## 增量迁移

启动通过 migrate_workbench 检查 workbench_schema：0→1 仅写配置标记，1 保持，未知未来版本拒绝打开。SQLite 表结构、旧登记 id、备份、凭据、projects.json 与正文不会被升级过程重写。

旧项目缺 phase 时从已有当前阶段或状态兼容显示；只有用户保存阶段 / 关系时写 STATUS。旧工作笔记和历史资源文件保留，不自动搬迁 / 删除。更换资料位置只保存引用路径，不隐式复制正文。

## 扩展边界

stage_prompt 是阶段总结生成接口的第一实现，提供上下文和可复制模板；不调用 AI、不自动执行文件内指令。后续可接入已有安全 Agent 调用，但须保持人工审阅与原文件写入保护。通用 Adapter 继续处理浅适配 Agent，不建立大量专用 UI 分支。

资源登记不构成 MCP Host、Skill 安装器或自动化调度器。原安全恢复确认及备份格式是稳定边界，日常 UI 改动不触及这些实现。
