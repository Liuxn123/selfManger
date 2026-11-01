# selfManger同类工具调研与功能建议

调研日期：2026-10-04，2026-10-05 补充 Skills Manager。以下依据项目官方说明与文档，未安装或实际试用这些产品；项目列出的能力不代表对所有 Agent 的兼容性。初次建议与本次实现范围分别说明。

决策更新：Skills/MCP/提示词页面已按用户选择移除，技能下载、导入和分发交由 Skills Manager。selfManger保留原有便携目录数据，但不再维护第二套技能库。

2026-10-15 新决策：Agent 页面改为 AI Agent 控制中心，并通过适配器能力展示具体操作。第一轮仅提供 Skills Manager 启动入口；用户随后明确授权获取 GitHub 源码并迁移部分功能。因此控制中心增加 Agent 原生目录发现，以及用户显式选择后的单项复制分配。不同于上游的 Tauri/Rust 客户端，本项目只移植路径映射和技能发现/分配思路，保持 PySide6 与现有资源/适配器结构；不迁移上游 UI、自动更新或链接分发。MIT 文本保存在 `licenses/skills-manager-MIT.txt`。

Skills Manager 官方仓库 README 描述了集中技能库、按 Agent/项目划分工作区、保留 Agent 原生技能位置，以及通过复制或链接部署的工作流。其仓库采用 MIT License；本项目参考这些产品机制，没有复制代码。来源：[Skills Manager 中文说明](https://github.com/xingkongliang/skills-manager/blob/main/README.zh-CN.md)、[MIT License](https://github.com/xingkongliang/skills-manager/blob/main/LICENSE)。

## v0.5.0：Skills Manager 参考与本次取舍

[Skills Manager 官方仓库](https://github.com/xingkongliang/skills-manager) 提供统一技能库、按 Agent 查看已有技能、自选目录、内容预览及复制/链接分发等能力。适合本项目借鉴的是“一个通用库，加各 Agent 实际文件清单”，可以独立接入现有 PySide6 页面和加密备份。

当时新增的“技能与工具”模块支持技能查看、本地导入、复制给选择的 Agent、通用提示词编辑和 MCP 配置清单；此模块后来已按用户选择移除。通用库曾保存在便携 data 中；没有复制该项目的代码或引入其 Tauri 框架。

自动联网技能市场、上游更新、双向同步、预设批量启停和链接分发未实现。跨电脑继续使用原来的加密备份或携带完整便携目录，不增加第二套同步与冲突规则。

## 调研了哪些工具

| 工具 | 项目公开说明中的重点 | 对 selfManger的启发 |
| --- | --- | --- |
| [Agent Deck](https://github.com/asheshgoplani/agent-deck) | 多 Agent 会话分组，运行/等待/空闲/错误状态，搜索，技能与 MCP 管理，远端会话 | 提醒只在需要处理时出现；区分“进程运行”和“任务等待输入” |
| [Agent Sessions](https://github.com/jazzyalex/agent-sessions) | macOS 本地历史搜索、浏览、支持的 CLI 会话续接、使用量查看 | 从旧记录回到原项目，减少找历史和手动定位的步骤 |
| [opcode](https://github.com/winfunc/opcode) | Claude Code 项目/会话管理，检查点、差异查看，MCP 配置和项目指令文件 | 恢复前展示变化；管理技能、MCP 和项目说明时先看清差异 |
| [Conductor](https://www.conductor.build/docs/concepts/parallel-agents) | 同一任务内的多 Agent 分工，以及分开工作区的独立任务 | 对独立任务明确项目、分支和工作区；按你目前习惯，先提供清楚的项目入口 |

Agent Deck 官方文档说明：某些 Agent 的准确任务状态依赖安装对应事件 hook。selfManger当前只检测进程，不应因此显示“正在思考”“等待你审批”等没有证据的状态。[来源：Agent Deck 状态检测与集成说明](https://github.com/asheshgoplani/agent-deck#status-detection)

Agent Sessions 的续接只针对支持的原生 CLI 会话。迁移文件、导出上下文、继续原会话是三种不同能力，不应承诺所有 Agent 可以互换完整会话。[来源：Agent Sessions](https://github.com/jazzyalex/agent-sessions#what-it-does)

## 最适合你先增加的三项

这是针对你“多电脑、Hermes 为主、多种工作 Agent、Obsidian、界面精简”的判断，不是上述产品的统一功能承诺。

| 顺序 | 建议 | 一个具体用法 | 界面怎么保持简单 |
| --- | --- | --- | --- |
| 1 | 继续工作入口 | 打开一个项目，同时找到对应笔记、最近记录、上次备份；支持的 Agent 可以回到原会话 | 放在现有项目详情中，一个“继续工作”按钮；配置可选 |
| 2 | 换电脑 / 恢复前检查 | 告诉你备份是否可解密、目标有没有冲突、原应用是否安装、哪些路径要重选；能比较时列出文件变化 | 在现有恢复预览增加“可以恢复 / 需要处理”清单，不另开模块 |
| 3 | 技能和 MCP 资料清单 | 看 Hermes、Codex 等各自用了哪些技能和工具，发现路径失效、缺少文件 | 放在现有 Agent 详情里，先只读；以后再支持单项迁移和带差异确认的修改 |

第 2 项已有完整性校验、空目录检查和恢复演练基础，可以逐步补齐迁移检查。技能 / MCP 清单应避免展示环境里的密钥；跨 Agent 配置格式不同，不能直接统一覆盖。

## 有用，但可以后做

- **项目交接便条：** 保存“做到哪里、下一步、关键文件”，可带到其他电脑，或复制给另一个 Agent。这是你保存的说明，不冒充原会话恢复。
- **少量待处理提醒：** 备份失败、较久未备份、服务器磁盘偏满时提示一次。Agent 等待输入的提醒要有可靠事件来源再增加。
- **备份变化摘要：** 告诉你这次新增、修改了多少文件，帮助决定是否备份大目录。

暂时不建议优先做完整聊天客户端、多 Agent 自动调度、大量图表或模型 / 账号集中切换。你的主要问题是资料保护和换电脑继续工作，这些功能会增加配置与维护成本。

## 已有功能与建议的区别

现在已有：资源登记、状态检查、备份、校验、项目/Agent 恢复演练、文件恢复、简单记录浏览与搜索、自动备份及便携口令库。

尚未实现：任意原应用会话续接、恢复前文件差异列表、跨 Agent 技能 / MCP 编辑、任务级等待输入检测、交接便条。
