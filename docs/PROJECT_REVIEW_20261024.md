# selfManger 全项目检查（2026-10-24）

同日后续：下列七项问题曾记录为已完成源码修复与专项回归；对应验证报告位于被忽略的本机 `qa/audit-fixes-20261024-verified/VALIDATION.md`，不随仓库分发，当前无法从本文链接复核。本文保留发现时的证据和失败基线；后续可在仓库中复核的相关回归包括[行动中心测试](../tests/test_action_center.py)、[项目任务事务测试](../tests/test_project_task_transactions.py)和[个人数据源测试](../tests/test_personal_data_source.py)，但这些测试不声称覆盖原报告全部七项。恢复环境及旧工作台测试遗留仍见T-124。

本轮检查覆盖源码目录、脚本、CI、依赖声明和全部测试模块；重点追踪任务保存/同步、后台执行、数据库版本和知识库切换的调用链。未修改产品源码或测试断言，未读取真实Vault/业务数据库，未联网执行Agent或发布。以下复现全部使用隔离虚构资料。

## 应先修复的问题

### 1. [P1] 项目任务同步失败后重试会重复创建

- 位置：`src/selfManger/project_task_documents.py:208`，`sync_project_task_documents`。
- 同步先逐条提交SQLite，再调用`_export_locked`把`new`行替换成实际ID。导出遇到磁盘/权限/外部编辑冲突失败时，数据库已有任务，但Markdown仍为`new`。
- 复现：相同文件连续同步两次，均只在导出阶段模拟OSError；数据库同名任务数从1变2，ID不同，原文仍为`new`。这里失败注入只模拟文件系统边界，不改变数据库逻辑。
- 影响：用户按错误提示重试即可造成重复任务，后续完成状态可能分裂。
- 建议：为导入建立稳定幂等身份，并让批量导入与文档回写具备可恢复的提交/补偿流程；至少保证失败后重试复用原ID。
- 证据：`qa/audit-20261024-project-sync-repro.py`、`qa/audit-20261024-project-sync-repro/results.json`。

### 2. [P1] 个人行动的数据库与Markdown分步提交

- 位置：`src/selfManger/action_center.py:298`（complete），同类问题也存在于save_plan/save_action；`personal_documents.py:150`。
- 数据库事务退出后才更新Markdown。即使preflight成功，后续磁盘错误或外部修改仍可使文档写入失败，此时已提交的数据库状态不回滚。
- 复现：创建带Markdown的行动，在实际文件写入边界模拟OSError；方法抛错，数据库为`done`，Markdown仍为`planned`。
- 影响：用户得到失败反馈，但部分结果已经生效；下一次外部编辑和同步可能重新导入旧状态。普通“刷新重试”不是完整的一致性保证。
- 建议：复用删除流程的分阶段文件变更思想，明确补偿/恢复记录；失败结果需要区分“完全未保存”和“部分提交”，禁止悄悄分叉。
- 证据：`qa/audit-20261024-repro.py`，`qa/audit-20261024-repro-final/results.json`中的`split_commit`。

### 3. [P1] 乐观锁检查与更新之间仍可丢失并发修改

- 位置：`src/selfManger/action_center.py:115`（save_plan），save_action也采用先SELECT检查再UPDATE。
- `_expected`检查没有先建立写事务，UPDATE本身也没有版本条件。两个线程能同时读到相同旧版本、同时检查通过，再各自提交，后写覆盖先写。
- 复现：两个真实线程，屏障只安排两次SELECT的时序；两次保存都传入同一`_expected`，均返回成功，最终只保留其中一个标题。
- 影响：多个窗口、快速连续后台编辑或同库其他客户端并发时，草稿保护提示可能失效。
- 建议：使用受写事务保护的读取比较，或带版本条件的原子UPDATE并检查rowcount；相关文件变更同时纳入一致性设计。
- 证据：同一`results.json`中的`lost_update`及`lost_update_final_title`。首轮复现夹具误拦截结果读取导致断言失败，修正为每线程仅拦截第一次SELECT后复现成功；初轮虚构目录保留。

### 4. [P2] 后台任务持久化失败仍发送成功信号

- 位置：`src/selfManger/ui/tasks.py:60`至81，`Worker.run`。
- `state`在保存结果前设为success。`save_result`或后续日志写入抛错后，通用异常分支没有改回failed，并用这个success状态保存错误报告及发信号。
- 复现：第一次finish_task模拟OSError、第二次恢复；最终信号为`success`，但outcome是含`error_type=OSError`的错误字典。
- 影响：窗口会按成功调用回调；回调可能错误提示完成、收到错误结构，或继续抛KeyError/TypeError。若再次持久化也失败，finally仍可能发出与结果不符的状态。
- 建议：区分业务结果与历史记录失败；错误路径必须产生一致的终态和结构，结果/日志失败还需有不依赖同一故障数据库的反馈通道。
- 证据：`worker_persistence_failure`复现结果。

### 5. [P2] 清空知识库配置后旧列表仍可点击并报错

- 位置：`src/selfManger/ui/knowledge.py:1290`和2180。
- root=None的分支只把index设为None并改提示，没有清理items、folders、详情和打开入口；open_entry随后直接调用self.index.record_access。
- 复现：加载两条虚构资料→清空Vault/工作区配置→刷新。index为None，列表仍有2条；点击原笔记得到`AttributeError: 'NoneType' object has no attribute 'record_access'`，外部打开调用为0。
- 建议：无库状态统一清空视图与选择、禁用相关操作；处理入口也校验当前索引/所属库，切库中的旧回调不得重新填入旧数据。
- 证据：`qa/audit-20261024-knowledge-repro.py`、`qa/audit-20261024-knowledge-results.json`。

### 6. [P2] 旧程序静默接受未来数据库版本

- 位置：`src/selfManger/storage.py:87`。
- `_migrate`将user_version>=6都视为可直接使用，只阻止重复迁移，没有拒绝高于当前支持版本的数据库。
- 复现：把虚构数据库user_version设为99；当前Store仍能打开并成功写入设置。
- 影响：用户在新版升级数据库后再用旧源码/旧便携包打开，旧代码仍可能改写不理解的新结构或语义。
- 建议：版本等于当前才跳过；高版本明确拒绝写入并提示使用对应版本。不得将测试中的99当真实用户数据库事实。
- 证据：`future_schema`复现结果。

### 7. [P2] AI任务取消不能中断正在执行的Codex子进程

- 位置：`src/selfManger/ai_task_execution.py:53`至68，`ui/window.py:1562`。
- 源码已确认：subprocess.run一次阻塞最长2小时，仅运行前后检查context.cancel；capture_output也没有体积上限。MainWindow只要jobs非空就拒绝关闭。
- 影响：执行期间点击取消只能等待子进程自然结束或两小时超时，客户端退出也受阻；大量输出会持续占用内存。
- 建议：改为有界流式输出与可轮询进程，取消/超时时明确处理子进程及其后代，并核对外部实际状态。
- 验证级别：静态调用链确认；本轮没有运行真实Codex，也没有宣称完成两小时实测。

## 工程与验证边界

- CI仍直接运行`unittest discover`，尚未使用逐模块运行器的超时与即时失败留存；一个模块卡住可能耗尽整个25分钟job。配置位置`.github/workflows/desktop.yml:48`。这是本地流程与CI的不一致，未查询远程CI历史。
- 旧`planning.py`和大型UI、12处空except候选、实际Vault规模及跨平台验证仍需继续；本轮没有因代码量大就认定功能有错。
- 生产备份/恢复临时目录的权限失败已扩展到多个测试模块；不能通过放宽生产目录权限来把环境失败包装成通过。详细测试统计与失败分类见本轮`qa/audit-20261024-tests/summary.json`及各模块日志。
- 旧`test_workbench_ui`仍存在DailyEntryDialog、more_actions等失效引用；应按当前产品契约重建覆盖，保留真正验证外部编辑、恢复与关闭生命周期的意图。

修复顺序建议：先解决1—3的数据一致性与重复导入，再处理4—6的错误路径和状态边界，随后处理7、遗留测试与真实环境验收。

## 本轮完整模块运行结果

命令：`.venv/Scripts/python scripts/run_tests.py --timeout 100 --output qa/audit-20261024-tests`。
47个模块均已得到结果，模块累计耗时766.8秒，退出码1：39个OK、6个FAIL、2个TIMEOUT。
OK模块共266项用例，含3项跳过；不能将266直接当作全部通过用例数，也不能把343项可发现用例当作全部已执行。

| 模块 | 结果 | 已观察到的阻碍 |
|---|---|---|
| test_archives | FAIL | 恢复暂存、Git临时目录及部分清理权限错误 |
| test_backup_repository | FAIL | 恢复暂存目录WinError5，另有宿主符号链接用例跳过 |
| test_portability | FAIL | SQLite快照私有临时目录不可访问，清理亦有权限错误 |
| test_simplified_manager | FAIL | SQLite/JSONL快照私有临时目录权限错误，另有文件占用清理错误 |
| test_ui_management | FAIL | 恢复任务PermissionError，暂存目录清理失败；设置用例通过 |
| test_usability | FAIL | SQLite快照、备份演练临时目录权限错误 |
| test_maintenance | TIMEOUT | 100秒截止；已有快照错误，线程栈位于tempfile.mkdtemp→apply_restore |
| test_workbench_ui | TIMEOUT | 100秒截止；已出现DailyEntryDialog、more_actions、resource_button旧引用及等待超时 |

以上是本环境观察，不代表所有失败根因都已排除，尤其备份/恢复仍需在正常权限环境复核。未修改系统ACL、跳过失败用例或调整断言来获得通过。所有模块原始日志和JSON都保留在`qa/audit-20261024-tests/`。
