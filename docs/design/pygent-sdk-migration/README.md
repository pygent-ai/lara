# lora → pygent SDK 迁移进展与下一步指南

> 最后更新：2026-11-14（会话 7，分支 `sdk-migration`，worktree `C:/Projects/lara-sdk-migration`）

> 本文档是跨 Agent 会话的交接基线。每轮工作结束后**必须**更新"当前状态"和"下一步"两节并随代码一起提交推送。

## 1. 目标与原则（用户已拍板，不要再议）

- **pygent 是 SDK**：lora 以 `pygent-ai` 为运行时，`src/lara` 只保留 lora 的产品层/适配层，**pygent 有的就直接 import，不维护副本**。
- **A：lora 适配层**建在 pygent 之上；**B（2026-11-14 改判）：agent 协作不再自研**——pygent 0.6.1 已把跨会话协作整个删掉，lara 跟进删除；所有 agent 任务派发（定时、后台起会话）统一走 **SDK scheduler**；用户消息一律走 SDK **`deliver_user_message`**（实时会话运行中 → fork 单次回复 + 信封交回主线；其他 → 排队新回合），lara 自己的 steering 删除。
- **不要兼容逻辑**：项目处于调试阶段、无外部用户。pygent 改名/改契约 → lora 直接跟进（含数据库里的值），不留迁移/兼容代码。
- **测试策略**：SDK 行为是基线。测试断言 lora 旧行为的 → 改成断 SDK 行为；测 lora 已删副本实现细节的 → 删除。手搓 history 字典一律改用 `message_to_dict(AIMessage(...))` 生成（与 SDK wire 合同同步）。
- **数据根**：仍为 `~/.lara`，**已定案不迁 `~/.pygent_v1`**（2026-11-14）：pygent 只是框架给的标准名，lara 是 agent 实现的产品，数据目录跟产品走。lora 的 `RunConfig.__post_init__` 先把 `user_pygent_root` 钉到 `~/.lara` 再委托 SDK，SDK fallback 不会触发。
- **KPI**：以删除代码量为主要考核点。

## 2. 当前状态（2026-11-14，会话 7）

- 依赖：**`pygent-ai[video]==0.6.1`**，PyPI 未发，`[tool.uv.sources]` 钉本地 `../pygent`（editable）。发版后删掉该覆盖、`uv lock`。
- `src/lara` 相对 `main`：**−2,900 行左右**（删 `dispatch/` 整包、`orchestration/session_collaboration.py`、`runtime/agent_collaboration.py`、`sessions/collaboration.py`、`sessions/directory.py`、`cli/automations.py`、`cli/projects.py`、`lara_api/routers|models/automations.py`；新增 `cli/scheduler.py` ~250 行、`lara_api/routers/scheduler.py` ~130 行）。
- 单测：`tests/unit` **658 passed / 0 failed / 1 xfailed**（`--ignore test_eternal_conversation.py`：那 8 个依赖本机不存在的 memory-cli 脚本）。
- `tests/scenario`：**28 passed / 0 failed**（原 7 红全部清零——它们假设的 `agent_start`/`session start` 同步语义随协作一起删了）。
- `pyright src/lara src/lara_api`：0/0/0。
- desktop：`npm test` 333 passed + 1 failed（`scripts/dev.test.mjs` 需要 Electron 二进制，worktree 用 `npm ci --ignore-scripts` 装的；renderer/main 全绿）。
- `src/lara` 中与 SDK 同名的文件：
  - `runtime/service.py`（~280 行）：`LaraRuntimeService(PygentRuntimeService)`，只做凭据注入、`run_config_loader=load_run_config`、Feishu speak 通道重注册，以及 lara_api 面（`cancel_turn`、`compress_session`、`execute_case`、task 查询、`recovery_case_run`）。**不再注册任何 lara 工具**；模型看到的额外工具就是 SDK 的 `schedule_task`/`emit_event`。
  - `sessions/manager.py`：SDK 子类 + run 查询 + `ensure(session_id)`（给调度器按稳定名创建会话；SDK `create` 不接受指定 id，见 upstream-issues #8）。
  - `orchestration/execution_host.py`：`LocalExecutionHost(SDK host)`，覆盖 `_build_runtime` 造 `LaraRuntimeService` 并接上 SDK 的 `on_exchange_ready`；lara_api 的 `ApiContext.execution_host` 就是它，`/chat/stream`、`/chat/sessions/{id}/messages`、Feishu bridge、CLI 全走 `deliver_user_message`。
  - `schema/models.py`：`RunConfig(SDK RunConfig)` 只重声明 `cli_bash_presets`（lara 的 CLI 预设）；`DelegationConfig` 直接用 SDK 的（`allowed_agents/max_parallel/background_enabled` 随协作删除）。

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
- 评估了第 5 节原第 1 步"适配器瘦身"，结论：不是净删除——会话 7 用户拍板后按 A(a) 落地（见下）。`cancel_turn` 的 fallback（无活动 turn 的 durable 孤儿执行）仍需走 runtime handle，`ManagedSessionTurn.cancel()` 只能取消本地驱动任务；`send_steering` 是 lora 独有的"向运行中执行注入 steering 消息"，SDK `deliver_user_message` 只有 interrupt-or-new-turn 两条路，且语义是按 session 路由而非按 execution，替换会改 API 行为。

### 会话 7 完成（用户决策：A 选 a，B 全量走 SDK scheduler）
- **升 0.6.1**（本地路径）。0.6.1 删了 `sessions/collaboration.py`、reminders 的 agent-message 收件箱，信封改走 react steering inbox（`on_exchange_ready` 钩子）；`AgentMessage.to_dict` 问题随之消失；`offload_remote_media` 的重复下载也修了（xfail 去掉）。
- **A(a)**：删 `send_steering`、chat_runner 的 `deliver_steering` 分流、`/chat/executions/{id}/steering`。新 `POST /chat/sessions/{id}/messages` 和 `/chat/stream` 都走 `execution_host.deliver_user_message`；实时会话运行中 → `ForkReplyRun` 把 fork 回复以一条 `pygent.v1.realtime.speak` + `execution.completed` 的合成流返回；普通会话 → 排队回合（desktop 的 pending chip 文案改为"已排队"，靠会话跟随循环在新回合开始时重新附着）。SDK 只对 `pygent.v1-says` 会话 fork；"所有会话都 fork"(b) 需要 pygent 把 `realtime_enabled_for` 做成可配置，已记 upstream-issues #9。
- **B**：删 `lara.dispatch` 整包、`lara automation` CLI、`/automations` 路由、`agent_start/send/status/list/wait` 工具、`lara session start|send|fork|status|wait`、`SessionDirectory`、`lara projects`。新增 `lara scheduler run|init-keys|add|list|show|approve|reject|pause|resume|delete|runs|emit`（`src/lara/cli/scheduler.py`，签名的 user submission）、`/scheduler/*` 路由、桌面"定时任务"页改为 SDK 任务模型（name/schedule/prompt/instructions/session/priority，状态 approved/needs_review/paused/rejected，agent 创建的任务在这里审批）。lara-api lifespan 在后台线程跑 `SchedulerDaemon.run_forever`，任务由 `lara --workspace-root <ws> session run <task.session> -m ...` 子进程执行。
- **删掉的能力（用户已接受）**：`agent_send` 跨会话消息、子会话完成回调父会话、`fork -m` 后台分叉。模型要起后台工作 → `schedule_task` 的 `at <now>` / `on <event>`；结果看调度器 run 日志。
- 顺带：`test_runtime_recovery.py` 的"取消进行中回合"用例改断 SDK 0.6.1 的新契约——取消时配对一条 `detached` tool 消息，不再留悬空 `tool_calls`（并修成确定性：等 `tool.authorized` 后再取消）。

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

1. **pygent 0.6.1 发 PyPI 后**：删 `pyproject.toml` 的 `[tool.uv.sources] pygent-ai = { path = "../pygent" }`，`uv lock`。
2. 向 pygent 推上游 [`upstream-issues.md`](upstream-issues.md)（现存 #2 `_child_agent_config`、#3 `IGNORED_DIRS`、#5 `fork_model_profiles` 稳定面、#6 `UPLOADS_SUBDIR`、#7 speak 通道/工具集扩展点、#8 `SessionManager.create` 指定 id、#9 `realtime_enabled_for` 可配置）。#1 和 #4 已在 0.6.1 修复。
3. 桌面端：实际跑一遍"普通会话运行中发消息 → 排队 → 跟随循环附着"的链路（只有 render 测试覆盖，没有交互验证）；`realtimeView.js` 里 `role === "automation"` 分支和 `pygent.v1.automation.trigger` 渲染已无生产者，可删。
4. 环境项：`test_eternal_conversation.py` 8 个红依赖 `C:/Projects/memory-cli/skills/dynamic-memory-cli/scripts/dynamic_memory_cli.py`；worktree 的 Electron 二进制未装（`npm ci --ignore-scripts`）。
5. 可选：Windows 自启动 `lara scheduler run`，让定时任务不依赖桌面 app 开着。

## 6. 已决策：用户消息投递（原"适配器瘦身"讨论）

用户拍板 **A(a)**：lara 不再有 steering；一律 `deliver_user_message`，SDK 现状只对「Lara说」实时会话 fork，普通会话排队。实现见会话 7。保留 `cancel_turn`（取消是另一个功能，孤儿执行仍需 runtime handle）。

## 7. 已决策：SDK scheduler 取代 lara dispatch

用户拍板 **B**：所有 agent 任务派发走 SDK scheduler，后续继续往这边收。实现见会话 7；两套并存的对照表作废。

## 8. 操作要点

- 查副本：`for f in $(find src/lara -name "*.py" | sed 's|src/lara/||'); do [ -f $PYGENT/src/pygent/agent/pygent_v1/$f ] && echo $f; done`（`$PYGENT=E:/Projects/pygent`，本地 pygent 仓与 PyPI 0.6.0 一致）。
- 删副本 → 全仓 `from lara.X import` 改 `from pygent.agent.pygent_v1.X import`（含 `__init__.py` 相对导入、测试里的 monkeypatch 字符串、`import ... as` 形式）。
- 不要用 `git rm --cached`/改 staging 之外的方式动索引；提交推送需用户授权（本轮已授权）。
- 跑全量加 `timeout 900`：若再出现挂死，优先怀疑测试调用了 SDK 已删除的 lora 旧 API（`AttributeError` 后后台任务未清理 → teardown 卡在 `_cancel_all_tasks`）。
