# lora → pygent SDK 迁移进展与下一步指南

> 最后更新：2026-11-14（会话 6，分支 `sdk-migration`，worktree `C:/Projects/lara-sdk-migration`）

> 本文档是跨 Agent 会话的交接基线。每轮工作结束后**必须**更新"当前状态"和"下一步"两节并随代码一起提交推送。

## 1. 目标与原则（用户已拍板，不要再议）

- **pygent 是 SDK**：lora 以 `pygent-ai` 为运行时，`src/lara` 只保留 lora 的产品层/适配层，**pygent 有的就直接 import，不维护副本**。
- **A：lora 适配层**建在 pygent 之上；**B：collaboration（agent 协作）由 lora 自研重写**，其余全部用 pygent 提供的。
- **不要兼容逻辑**：项目处于调试阶段、无外部用户。pygent 改名/改契约 → lora 直接跟进（含数据库里的值），不留迁移/兼容代码。
- **测试策略**：SDK 行为是基线。测试断言 lora 旧行为的 → 改成断 SDK 行为；测 lora 已删副本实现细节的 → 删除。手搓 history 字典一律改用 `message_to_dict(AIMessage(...))` 生成（与 SDK wire 合同同步）。
- **数据根**：仍为 `~/.lara`，**已定案不迁 `~/.pygent_v1`**（2026-11-14）：pygent 只是框架给的标准名，lara 是 agent 实现的产品，数据目录跟产品走。lora 的 `RunConfig.__post_init__` 先把 `user_pygent_root` 钉到 `~/.lara` 再委托 SDK，SDK fallback 不会触发。
- **KPI**：以删除代码量为主要考核点。

## 2. 当前状态（2026-11-14）

- 依赖：`pygent-ai[video]==0.6.0`（`pyproject.toml` / `uv.lock`）。
- `src/lara`：124 → **60 个文件**；会话 5+6 相对 `main` 净删 **~980 行**（`src` −779）。
- 单测：`tests/unit` **730 passed / 10 failed / 2 xfailed**。8 个在 `test_eternal_conversation.py`，是环境问题（依赖本机不存在的 `C:/Projects/memory-cli/.../dynamic_memory_cli.py`），HEAD 上同样红；2 个 `test_bash_background.py::test_background_completion_finalizes_audit_and_late_file_writes[cancel|restore]` 只在全量下偶发超时，单跑全绿（HEAD 同样）。第 4 节原 7 个 collaboration 失败**已清零**。
- `pyright src/lara src/lara_api`：**0 个代码错误**（仅剩 2 个 `keyring` 可选依赖未装的 `reportMissingImports`，和 `core/io.py` 的 wildcard re-export 警告）。
- `tests/scenario`：**25 passed / 7 failed**（原 10 红）。剩余 7 个全是 dispatch 设计遗留，见第 5 节第 1 步。
- desktop：`npm test` 334 passed（在 `main` 工作区验证；worktree 没装 node_modules）。
- `src/lara` 中与 SDK 同名的文件只剩：
  - `runtime/service.py`：**适配器** `LaraRuntimeService(PygentRuntimeService)`，补 lara_api 面（`send_steering`、`cancel_turn`、`compress_session`、`execute_case`、`recovery_case_run`、`get_task*`/`cancel_task`）+ 凭据注入 + **agent 协作工具面**（`agent_start/send/status/list/wait` 的 `AgentToolExecutor`、访问控制、`_child_agent_config` 用 lora 的 `RunConfig`/`load_run_config`）。
  - `sessions/manager.py`：SDK `SessionManager` 子类 + lora 产品层（directory 索引、run 查询、`LARA_SAYS_SESSION_MODE = PYGENT_SAYS_SESSION_MODE`）。
  - `schema/models.py`：394 → ~190 行。**`RunConfig(SDK RunConfig)` / `DelegationConfig(SDK DelegationConfig)` 是 SDK 子类**：`RunConfig` 只重声明 `cli_bash_presets`（lora 的 CLI 预设）和 `delegation`（lora 的 `allowed_agents/max_parallel/background_enabled`），`__post_init__` 先把 `user_pygent_root` 钉到 `~/.lara` 再调 SDK 的。lora 独有域只剩 `CaseDefinition`/`CaseRunResult`/`EvaluationResult`/`WorkspaceRef` 和 `case_run_resumable(status)`。因为是子类，SDK 里所有标注为 SDK `RunConfig` 的参数都能直接传 lora 的；反向（SDK 属性 `self.config`/`manager.config` 当 lora 用）在 `LaraRuntimeService` 和 `SessionManager` 各加一行 `config: RunConfig  # pyright: ignore[reportIncompatibleVariableOverride]`。
  - `sessions/collaboration.py`：796 → **~55 行**。只做一件事：给 SDK `AgentMessage` 打 `to_dict` 补丁（`AgentMessage.to_dict = agent_message_to_dict`，SDK pipeline 渲染收件箱时调用它但 SDK 没定义——第 3 节），外加 `collaboration_item_dict(item)` 给工具面/CLI 用。`SessionCollaborationStore`/`AgentMessageState` 直接 re-export。legacy `collaboration_operations` 表、`CollaborationOperation`/`CollaborationState` 整删，操作状态只走 `lara.dispatch`。
  - `runtime/reminders/__init__.py`：纯 re-export。
- 其余 lora 独有模块为薄 shim（SDK 显式 re-export + lora 独有符号）：`core/io.py`（`file_snapshot`/`read_jsonl_snapshot`）、`core/process.py`（`owner_is_foreign_and_alive`）、`runtime/model_configuration.py`（`discover_models`/`builtin_model_catalogs`/`lara_credential_environment`）、`runtime/realtime/nudge.py`（`should_nudge`）、`runtime/context.py`（`PygentContext` 别名）、`runtime/compression.py`（`SessionCompressionModule`，SDK 无手动压缩入口）。

### 本轮（会话 3）完成
- 整删 24 个纯副本 + `tools.py` + `eternal_conversation.py` shim；5 个文件瘦身为 re-export。
- 全量挂死根因：lora 旧 Harness 的 `wait_idle`/`retry_pending`/`file_command` 是 SDK 刻意不提供的 API → 测试改用测试本地 `_drain()` + `record_and_trigger` 续跑；`scripts/acceptance/eternal_conversation_200.py` 同步改接。
- 对齐 SDK 行为：extractor 需显式 `foreground_system_prompt`（SDK 去掉兜底）、指令标签 `<memory-extract-instruction>`、事件 kind `lara.*` → `pygent.v1.*`（src/tests/desktop 共 75 处）。
- `continuation` 一整类失败清零（根因全是 fixture 手搓 legacy dict，含恢复测试子进程 fixture）。
- **真实运行时回归已修**：SDK `CredentialEnvironment` 只读进程环境变量 → 新增 `lara_credential_environment()` 注入 lora 的 `credentials.env` + keyring（适配器 `__init__` 与 `lara_api/routers/settings.py` 统一走它）。

### 会话 4 完成
- 清掉第 2、3 桶（11 个）：测试桩补齐 SDK 新契约（`send_interrupt` 无 `execution_id`、finalizer 的 `sessions_root`/`pending_agent_messages`、`reminders.turn_opening_claim_id/claim_turn_opening`）；`EventStore` 去掉 lora 独有的 `list_by_session/case/turn`（用 `list_events()` 投影）；speak 判定跟 SDK（看本轮 ToolMessage 的 results 而非 context 的 tool_calls）；模型组命名空间 `pygent.v1:<group>`。
- `compress_session` 补回被我上一轮丢掉的 checkpoint 持久化（`context_to_dict(... LARA_CONTEXT_CODECS)` 写回 `session.metadata["agent_context"]`）。
- `schema/models.py` 再删 351 行（15 个同构类别名到 SDK）。
- 新发现 1 个 SDK bug（见第 3 节 media 缓存），`xfail(strict)`。
- **注意**：本地 `E:/Projects/pygent` 仓已领先于已安装的 PyPI 0.6.0（例如 reminders API 已改名 `claim_initial`）。lora 的基线是**已安装版本**（`.venv/Lib/site-packages/pygent`），核对 SDK 行为时以它为准，不要看本地仓 HEAD。

### 会话 5 完成
- **lara-says 收尾**：desktop 漏改的两处常量跟 SDK（`realtimeView.js` `LARA_SAYS_SESSION_MODE = "pygent.v1-says"`、`App.jsx` `INTERNAL_TOOL_ID_PREFIX = "pygent.v1.internal."`），desktop 3 个红项清零。
- **collaboration 自研（B）完成**，第 4 节 7 个用例全绿：
  - `LaraRuntimeService` 注册 `lara.agent.*` 的 `AgentToolExecutor`（`sandbox_support` 走 `SandboxExecutorSupport`），`external_tools` 按 `visible_agent_collaboration_specs` 过滤（SDK 的 `pygent.v1.scheduler.*` 工具不暴露给模型——lora 的定时任务走自己的 `lara automation`）；因 SDK 模板 agent 在 `super().__init__` 里已用 SDK 工具集构建，构造后清空 `_agent_definitions` 重建一次。
  - 默认 `reminders` 换成 lora 的 `ReminderService`（见第 2 节），解决 SDK pipeline 对 `AgentMessage.to_dict` 的依赖。
  - 覆盖 `_child_agent_config`：SDK 版用 SDK `RunConfig.from_dict`，会拒绝 lora 的 `DelegationConfig.allowed_agents`。
- **legacy 整删**：`collaboration_operations` 表及其 `reserve/claim/heartbeat/finish/...`、`SessionCollaborationService` 里的 legacy 回退（`list_operations`/`status` 只查 dispatch；未知 id 抛 `KeyError`）；对应 5 个 store 测试删除，`test_agent_collaboration_tools.py` 的桩改用 `DispatchOperationView`，`tests/scenario/test_agent_orchestration_e2e.py` 改读 `DispatchStore`（该用例 HEAD 上就红，需要跑 dispatch scheduler，留给第 5 节第 3 步）。
- `test_realtime_scenarios.py` 的桩 `release=asyncio.sleep(0)` 改为 callable，消掉后台 `TypeError` 噪音。

### 会话 6 完成
- **`RunConfig` 继承 SDK**（见第 2 节）：pyright 的 `RunConfig` 不兼容一族清零；`tests/conftest.py` 的 `project_pygent_root` 重定向改 patch SDK 模块（lora 的 `__post_init__` 现在委托 SDK 算 `pygent_root`）。
- **`AgentMessage.to_dict` 改为对 SDK 类打补丁**，删掉会话 5 的 `AgentMessage`/`SessionCollaborationStore`/`ReminderService` 三个子类；`LaraRuntimeService` 不再替换 `reminders`。
- **再删 `runtime/service.py` 里 4 个 SDK 副本**：`_SpeakExecutor`、`_DiffExecutor`、`_uncovered_conversation_messages`、`MEMORY_JOB_LIMITS`（测试改从 SDK import）。顺带修了一个**真实回归**：lora 的 `_SpeakExecutor` 副本从没被注册过，SDK 的以 `channels=()` 注册，所以 Feishu 外发在 `main` 上是断的；现在 `__init__` 用 `replace_existing=True` 重注册 SDK `_SpeakExecutor(self, channels=load_feishu_channels(...))`。`test_realtime_feishu.py` 的 `emit` 桩改为 SDK 的位置参数签名 `(kind, data)`。
- `tests/scenario/test_cli_flow.py` 的 4 处 `"Lara agent is wired into chat"` 改断 SDK 文案 `"pygent_v1 agent is wired into chat"`（5 个场景转绿）。
- 评估了第 5 节原第 1 步"适配器瘦身"，**结论：不是净删除，暂不做，待与用户讨论**（见第 6 节）。`cancel_turn` 的 fallback（无活动 turn 的 durable 孤儿执行）仍需走 runtime handle，`ManagedSessionTurn.cancel()` 只能取消本地驱动任务；`send_steering` 是 lora 独有的"向运行中执行注入 steering 消息"，SDK `deliver_user_message` 只有 interrupt-or-new-turn 两条路，且语义是按 session 路由而非按 execution，替换会改 API 行为。

## 3. 已知 SDK 缺口（需推上游 pygent）

完整报告（位置、复现、建议修复、lara 侧可回收的代码）见 [`upstream-issues.md`](upstream-issues.md)。

| 缺口 | 现状 | 处理 |
|---|---|---|
| `FileEffectTracker.IGNORED_DIRS` 硬编码 `.pygent_v1`，lora 的 `.lara` 目录不会被忽略 | `tests/unit/test_tools.py::test_file_effect_tracker_ignores_runtime_directories` 标为 `xfail(strict)` | 推上游：从 `RunConfig` 读取可忽略目录 |
| `fork_model_profiles` 不在 `pygent.agent` 稳定面，lora 直接 import `pygent.agent.pygent_v1.runtime.realtime.fork_reasoning` | 可用，但 pygent 改内部路径会断 | 推上游提升到稳定面 |
| SDK 无手动 `compress_session` 入口 | lora `runtime/compression.py` 用 SDK 原语实现（用户拍板） | 维持 |
| `graph.media_offload.offload_remote_media` 对每个节点传新 `fetched={}`，同一 URL 被重复下载（与其 docstring "downloaded once" 矛盾） | `tests/unit/test_media_offload.py::test_same_remote_url_downloaded_once` 标为 `xfail(strict)` | 推上游：把缓存字典提到入口作用域 |
| `graph/pipeline.py` 渲染收件箱调用 `AgentMessage.to_dict(include_content=True)`，但 SDK `sessions/collaboration.AgentMessage` 没有 `to_dict` | lora 用 `AgentMessage` 子类 + store 子类补上（`sessions/collaboration.py`） | 推上游：给 SDK `AgentMessage` 加 `to_dict`，之后 lora 的子类和 `runtime/reminders` shim 可删 |
| SDK `_child_agent_config` 用 SDK `RunConfig.from_dict` 复制配置，宿主扩展过的 `RunConfig` 字段（lora `DelegationConfig.allowed_agents`）被拒 | lora 覆盖 `_child_agent_config` | 推上游：改用 `type(self.config).from_dict` 或走 `run_config_loader` |

## 4. collaboration 验收（已完成）

原 7 个 collaboration 失败用例（`test_agent_collaboration_tools.py` ×2、`test_runtime_service.py` ×2、`test_native_agent_capabilities.py` ×1、`test_realtime_scenarios.py` ×2）会话 5 全部通过。运行：`.venv/Scripts/python.exe -m pytest tests/unit -q -p no:cacheprovider`。

剩余 10 个红全部是 `test_eternal_conversation.py` 依赖外部 `C:/Projects/memory-cli` 脚本，环境缺失所致。

## 5. 下一步（按序）

1. **`tests/scenario` 剩余 7 红需要产品决策，不是迁移问题**：`test_agent_orchestration_e2e.py`、`test_cli_flow.py::test_session_collaboration_cli_starts_background_turn_and_queues_send`、`test_session_completion_callback_cli_e2e.py` 都假设 `agent_start`/`lara session start` **同步**创建子会话并返回 `target_session_id`、`lara session wait` 无需调度器就能等到结果——这是 `64fe1fa feat(dispatch)` 之前的设计；dispatch 之后 start 只入队，要有 `DispatchScheduler`（lara-api 进程里）来驱动。两条路：CLI 的 `start/wait` 内联跑一个 scheduler，或把这三个场景改成起 `DispatchScheduler` 再断言。另外 `test_cli_flow.py` 其余 4 个和 `test_session_completion_callback` 之一也受同一假设影响，修完上面的会一起绿。
2. 向 pygent 推上游第 3 节缺口（4 个 bug/缺口 + fork_reasoning 稳定面）。`AgentMessage.to_dict` 和 `_child_agent_config` 两项修了之后，lora 的 `sessions/collaboration.py` 补丁和 `LaraRuntimeService._child_agent_config` 覆盖可整删。
3. 决策点：SDK 的 `pygent.v1.scheduler.*`（`schedule_task`/`emit_event`）工具目前对模型隐藏（`external_tools` 只含 lora 的 `lara.agent.*`）。若改为直接用 SDK 调度器，`lara automation` 一整套可删，但是产品行为变更。
4. 环境项（不需要决策）：`test_eternal_conversation.py` 8 个红依赖 `C:/Projects/memory-cli/skills/dynamic-memory-cli/scripts/dynamic_memory_cli.py`，装回即可；`keyring` 属 `credentials` dependency-group，`uv sync --group credentials` 后 pyright 全绿；`test_bash_background.py::test_background_completion_finalizes_audit_and_late_file_writes` 全量下偶发超时，单跑全绿。
5. 已完成的可选项：`examples/minimal_agent` 钉到 0.6.0（import 已验证）；`README.md` 与 `docs/design/realtime-interaction/README.md` 的旧钉版说明已更新（历史报告里的版本号是史实，保留）；`core/io.py` 去掉 wildcard import，显式 re-export。

## 6. 待讨论：适配器瘦身（原第 5 节第 1 步）

文档早先建议把 `LaraRuntimeService.cancel_turn` 换成 `ManagedSessionTurn.cancel()`、`send_steering` 换成 SDK `LocalExecutionHost.deliver_user_message`，我（会话 6）评估后暂不做，理由如下，待用户确认：

| 项 | 现状 | 为什么不是净删除 |
|---|---|---|
| `cancel_turn(execution_id)` | `ChatRunRegistry.cancel_turn` 先找活动 turn，找不到就 `acquire_runtime()` 再调 `runtime_service.cancel_turn` | `ManagedSessionTurn.cancel()` 只 `task.cancel()` 本进程驱动的任务；进程重启后留下的 durable 孤儿执行没有 `ManagedSessionTurn`，必须通过 `runtime.get_execution_handle(execution_id).cancel()`。保留 `cancel_turn` 只有 4 行。 |
| `send_steering(execution_id, ...)` | 向**运行中的 execution** 注入一条 `pygent.v1.user.steering` 投影操作（immediate/wait 两种模式，带附件） | SDK `deliver_user_message` 是按 **session** 路由：realtime 且有活动 turn → fork interrupt；否则起新 turn。它没有"给正在跑的 execution 插一条 steering 消息"这条路。换过去等于删掉 steering 功能，是行为变更不是重构。 |
| chat_runner 的 fork-or-turn 分流 | `deliver_steering` 里 realtime → `send_interrupt`，否则 → `send_steering` | 分流本身依赖 `send_steering` 存在；若保留 steering，这段分流就是必需的。 |

如果用户决定**放弃 steering 功能**（用户插话一律走 interrupt/new turn），那三项可以一起删，`LaraRuntimeService` 再少 ~70 行，chat_runner 的 `deliver_steering` 可换成 `host.deliver_user_message`。

## 7. 待讨论：SDK scheduler vs lara dispatch

两套调度并存，见第 5 节第 3 步。事实整理：

- **SDK**（`pygent.agent.pygent_v1.scheduler`）：独立守护进程 `pygent_v1 scheduler run`，自己的 SQLite（`<pygent_root>/scheduler/pygent.v1-scheduler.sqlite3`，签名的 `submissions` + daemon 独占的 `tasks/runs`），到点用子进程跑 `python -m pygent_v1 session run <scheduler-session> -m <prompt>`。模型工具 `schedule_task`/`emit_event` 只能**追加 submission**，由 daemon 审批/提升。只有"定时/事件 → 跑一轮会话"这一种任务。
- **lara**（`lara.dispatch`）：`DispatchScheduler` 跑在 **lara-api 进程里**（`lara_api/app.py` lifespan 启动），轮询 `~/.lara/dispatch-v1.sqlite3` 的 `dispatch_items`，三种 kind：`turn`（automation 定时触发的一轮）、`start`（`agent_start` 后台起子会话）、`fork`。CLI `lara automation ...` / `lara session start|fork` 直接往这个 sqlite 写行；没有 lara-api 在跑时只入队不执行——这就是 `tests/scenario` 剩余 7 红的原因。
- **差异**：SDK 没有 `start`/`fork`（agent 协作的后台启动），也没有运行中回合完成后回调父会话（`_notify_completion`）；lara 没有签名/审批门。如果换 SDK scheduler，`start`/`fork` 仍要 lara 自己调度。

## 8. 操作要点

- 查副本：`for f in $(find src/lara -name "*.py" | sed 's|src/lara/||'); do [ -f $PYGENT/src/pygent/agent/pygent_v1/$f ] && echo $f; done`（`$PYGENT=E:/Projects/pygent`，本地 pygent 仓与 PyPI 0.6.0 一致）。
- 删副本 → 全仓 `from lara.X import` 改 `from pygent.agent.pygent_v1.X import`（含 `__init__.py` 相对导入、测试里的 monkeypatch 字符串、`import ... as` 形式）。
- 不要用 `git rm --cached`/改 staging 之外的方式动索引；提交推送需用户授权（本轮已授权）。
- 跑全量加 `timeout 900`：若再出现挂死，优先怀疑测试调用了 SDK 已删除的 lora 旧 API（`AttributeError` 后后台任务未清理 → teardown 卡在 `_cancel_all_tasks`）。
