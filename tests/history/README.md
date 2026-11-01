# 已移除接口的历史测试

这里保留移除的 Agent 管理/Token 用量/旧仪表盘和物理搬移归档接口用例原文，不参与现行 unittest。现行六页导航、统一任务、状态归档与改名迁移由当前测试维护。没有恢复已移除功能来迎合旧断言。

2026-10-27：`test_workbench_ui-before-20261027.txt`完整保留27条旧整窗用例，SHA-256为`4b2abf1a7240963967d2cad6af50edba861de44af39f1afc5183f10b23092995`。现行`test_workbench_ui.py`验证六入口、跨页/Markdown行动、项目新建筛选、状态归档保留目录、缺文档、无效工作区、未来浏览及空工作区。其余原意映射：知识库增删/检索/外部编辑→test_knowledge_add、test_knowledge_incremental、test_knowledge；设置/安全→test_settings_layout、test_ui_management；计划范围/周期/项目筛选→test_plan_modules、test_project_plan_filter、test_repeat_options；响应过期/关闭→test_navigation_responsiveness、test_action_center；布局→test_usability、test_project_simplification；物理归档、旧Agent/资源页、DailyEntryDialog、旧Overview切换等接口已退役，不恢复旧API迎合断言。

2026-10-28：按用户要求移除 Hermes、通用 Agent 备份/会话和应用内 Codex 执行后，将专属用例保存为 `test_backup_repository.py.removed.txt`、`test_hermes_library.py.removed.txt`、`test_portability_agent_removed.txt`、`test_agent_hermes_removed_cases.txt`、`test_hermes_dialog_removed.txt`、`test_codex_execution_removed.txt`；不参与当前测试。项目 Agent 文档和项目 Skills 的现行测试继续运行。历史文件不是用户业务备份。
