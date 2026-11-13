# lora → pygent SDK 迁移进展与下一步指南

> 最后更新：2026-11-13（会话 4）

> 本文档是跨 Agent 会话的交接基线。每轮工作结束后**必须**更新"当前状态"和"下一步"两节并随代码一起提交推送。

## 1. 目标与原则（用户已拍板，不要再议）

- **pygent 是 SDK**：lora 以 `pygent-ai` 为运行时，`src/lara` 只保留 lora 的产品层/适配层，**pygent 有的就直接 import，不维护副本**。
- **A：lora 适配层**建在 pygent 之上；**B：collaboration（agent 协作）由 lora 自研重写**，其余全部用 pygent 提供的。
- **不要兼容逻辑**：项目处于调试阶段、无外部用户。pygent 改名/改契约 → lora 直接跟进（含数据库里的值），不留迁移/兼容代码。
- **测试策略**：SDK 行为是基线。测试断言 lora 旧行为的 → 改成断 SDK 行为；测 lora 已删副本实现细节的 → 删除。手搓 history 字典一律改用 `message_to_dict(AIMessage(...))` 生成（与 SDK wire 合同同步）。
- **数据根**：仍为 `~/.lara`（lora 的 loader 显式注入 `RunConfig.user_pygent_root/pygent_root`，SDK 的 `~/.pygent_v1` fallback 不会触发）。
- **KPI**：以删除代码量为主要考核点。

## 2. 当前状态（2026-11-13）

- 依赖：`pygent-ai[video]==0.6.0`（`pyproject.toml` / `uv.lock`）。
- `src/lara`：124 → **60 个文件**；相对 HEAD 纯删除 66 文件 / 12.7k 行；全仓 diff +1.3k / −17.6k。
- 单测：`tests/unit` **738 passed / 7 failed / 2 xfailed**（全量 ~2.5 分钟，无挂死）。剩余 7 个全部属于 collaboration（B 自研范围），见第 4 节。
- `src/lara` 中与 SDK 同名的文件只剩：
  - `runtime/service.py`：**适配器** `LaraRuntimeService(PygentRuntimeService)`，只补 lara_api 面（`send_steering`、`cancel_turn`、`compress_session`、`execute_case`、`recovery_case_run`、`get_task*`/`cancel_task`）+ 凭据注入。
  - `sessions/manager.py`：SDK `SessionManager` 子类 + lora 产品层（directory 索引、run 查询、`LARA_SAYS_SESSION_MODE = PYGENT_SAYS_SESSION_MODE`）。
  - `schema/models.py`：lora 配置 schema（`RunConfig`、`DelegationConfig`、`CaseDefinition`/`CaseRunResult`/`EvaluationResult`/`WorkspaceRef` 等 lora 独有域）。15 个与 SDK 同构的类（`CaseRunRef`/`CaseRunStatus`/`SessionRef`/`AgentSession`/`ContextEvent`/各 `*Config`/`BashCliPreset`）已直接从 `pygent.agent.pygent_v1.schema.models` 导入——dataclass 相等性与 `isinstance` 要求同一类。lora 加的 `CaseRunStatus.resumable` 改为模块函数 `case_run_resumable(status)`。
  - `sessions/collaboration.py`：按 B 决策待自研重写。
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

## 3. 已知 SDK 缺口（需推上游 pygent）

| 缺口 | 现状 | 处理 |
|---|---|---|
| `FileEffectTracker.IGNORED_DIRS` 硬编码 `.pygent_v1`，lora 的 `.lara` 目录不会被忽略 | `tests/unit/test_tools.py::test_file_effect_tracker_ignores_runtime_directories` 标为 `xfail(strict)` | 推上游：从 `RunConfig` 读取可忽略目录 |
| `fork_model_profiles` 不在 `pygent.agent` 稳定面，lora 直接 import `pygent.agent.pygent_v1.runtime.realtime.fork_reasoning` | 可用，但 pygent 改内部路径会断 | 推上游提升到稳定面 |
| SDK 无手动 `compress_session` 入口 | lora `runtime/compression.py` 用 SDK 原语实现（用户拍板） | 维持 |
| `graph.media_offload.offload_remote_media` 对每个节点传新 `fetched={}`，同一 URL 被重复下载（与其 docstring "downloaded once" 矛盾） | `tests/unit/test_media_offload.py::test_same_remote_url_downloaded_once` 标为 `xfail(strict)` | 推上游：把缓存字典提到入口作用域 |

## 4. 剩余 7 个失败 —— 全部是 collaboration（并入 B 自研）

运行：`.venv/Scripts/python.exe -m pytest tests/unit -q -p no:cacheprovider`

| 用例 | 现象 |
|---|---|
| `test_agent_collaboration_tools.py::test_model_tools_share_the_injected_session_collaboration_service` | `LaraRuntimeService` 缺 `_wait_for_agent_collaborations` |
| `test_agent_collaboration_tools.py::test_wait_any_checks_every_id_before_waiting` | 同上一族 |
| `test_runtime_service.py::test_runtime_exposes_only_available_agent_collaboration_tools` | SDK 暴露了 `pygent.v1.scheduler.*` 工具；lora 期望按可用性过滤 |
| `test_runtime_service.py::test_agent_collaboration_uses_pygent_agent_tool_executor` | `lara.agent.agent_start@1` executor 未注册 |
| `test_native_agent_capabilities.py::test_agent_message_is_appended_after_tool_result_without_starting_a_turn` | SDK 返回的 `AgentMessage` 无 `to_dict`（lora 旧 collaboration 的消息类有） |
| `test_realtime_scenarios.py::test_mid_turn_interrupt_forks_reply_and_delivers_envelope` | 同 `AgentMessage.to_dict`（信封投递走 collaboration 消息存储） |
| `test_realtime_scenarios.py::test_consecutive_interrupts_produce_two_replies_and_envelopes` | 同上 |

这 7 个就是 collaboration 自研的验收清单：lora 侧重写消息存储（含 `to_dict`）与 `agent_start/agent_send/agent_wait` 工具面并注册 executor；SDK 的 `sessions/collaboration.py` 不用。

## 5. 下一步（按序）

1. **collaboration 自研**（B）：在 lora 重写存储 + 工具面（`agent_start/agent_send/agent_wait` 等），接 SDK 的 `ToolSpec`/executor 注册；验收 = 第 4 节 7 个用例全绿。SDK 的 `sessions/collaboration.py` 不用。
2. 适配器继续瘦身：`cancel_turn` 可改为 chat_runner 直接用 `ManagedSessionTurn.cancel()`；`send_steering` 与 chat_runner 的 fork-or-turn 分流可整体换成 SDK `LocalExecutionHost.deliver_user_message`（净删除分流代码）。
3. `pyright` 全量（迄今只跑了 pytest）；`tests/scenario` 基线清理（升级前就有 10 个红）。
4. 向 pygent 推上游第 3 节缺口（2 个 bug/缺口 + fork_reasoning 稳定面）。
5. 可选：数据根是否迁 `~/.pygent_v1`；`examples/minimal_agent` 钉版升级；`docs/` 旧版本号清理。

## 6. 操作要点

- 查副本：`for f in $(find src/lara -name "*.py" | sed 's|src/lara/||'); do [ -f $PYGENT/src/pygent/agent/pygent_v1/$f ] && echo $f; done`（`$PYGENT=E:/Projects/pygent`，本地 pygent 仓与 PyPI 0.6.0 一致）。
- 删副本 → 全仓 `from lara.X import` 改 `from pygent.agent.pygent_v1.X import`（含 `__init__.py` 相对导入、测试里的 monkeypatch 字符串、`import ... as` 形式）。
- 不要用 `git rm --cached`/改 staging 之外的方式动索引；提交推送需用户授权（本轮已授权）。
- 跑全量加 `timeout 900`：若再出现挂死，优先怀疑测试调用了 SDK 已删除的 lora 旧 API（`AttributeError` 后后台任务未清理 → teardown 卡在 `_cancel_all_tasks`）。
