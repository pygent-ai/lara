# lora → pygent SDK 迁移进展与下一步指南

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
- 单测：`tests/unit` **728 passed / 18 failed / 1 xfailed**（全量 ~3 分钟，无挂死）。
- `src/lara` 中与 SDK 同名的文件只剩：
  - `runtime/service.py`：**适配器** `LaraRuntimeService(PygentRuntimeService)`，只补 lara_api 面（`send_steering`、`cancel_turn`、`compress_session`、`execute_case`、`recovery_case_run`、`get_task*`/`cancel_task`）+ 凭据注入。
  - `sessions/manager.py`：SDK `SessionManager` 子类 + lora 产品层（directory 索引、run 查询、`LARA_SAYS_SESSION_MODE = PYGENT_SAYS_SESSION_MODE`）。
  - `schema/models.py`：lora 配置 schema（含 dispatch/evaluation 等 lora 独有域；`BashCliPreset` 已直接复用 SDK 类）。
  - `sessions/collaboration.py`：按 B 决策待自研重写。
- 其余 lora 独有模块为薄 shim（SDK 显式 re-export + lora 独有符号）：`core/io.py`（`file_snapshot`/`read_jsonl_snapshot`）、`core/process.py`（`owner_is_foreign_and_alive`）、`runtime/model_configuration.py`（`discover_models`/`builtin_model_catalogs`/`lara_credential_environment`）、`runtime/realtime/nudge.py`（`should_nudge`）、`runtime/context.py`（`PygentContext` 别名）、`runtime/compression.py`（`SessionCompressionModule`，SDK 无手动压缩入口）。

### 本轮（会话 3）完成
- 整删 24 个纯副本 + `tools.py` + `eternal_conversation.py` shim；5 个文件瘦身为 re-export。
- 全量挂死根因：lora 旧 Harness 的 `wait_idle`/`retry_pending`/`file_command` 是 SDK 刻意不提供的 API → 测试改用测试本地 `_drain()` + `record_and_trigger` 续跑；`scripts/acceptance/eternal_conversation_200.py` 同步改接。
- 对齐 SDK 行为：extractor 需显式 `foreground_system_prompt`（SDK 去掉兜底）、指令标签 `<memory-extract-instruction>`、事件 kind `lara.*` → `pygent.v1.*`（src/tests/desktop 共 75 处）。
- `continuation` 一整类失败清零（根因全是 fixture 手搓 legacy dict，含恢复测试子进程 fixture）。
- **真实运行时回归已修**：SDK `CredentialEnvironment` 只读进程环境变量 → 新增 `lara_credential_environment()` 注入 lora 的 `credentials.env` + keyring（适配器 `__init__` 与 `lara_api/routers/settings.py` 统一走它）。

## 3. 已知 SDK 缺口（需推上游 pygent）

| 缺口 | 现状 | 处理 |
|---|---|---|
| `FileEffectTracker.IGNORED_DIRS` 硬编码 `.pygent_v1`，lora 的 `.lara` 目录不会被忽略 | `tests/unit/test_tools.py::test_file_effect_tracker_ignores_runtime_directories` 标为 `xfail(strict)` | 推上游：从 `RunConfig` 读取可忽略目录 |
| `fork_model_profiles` 不在 `pygent.agent` 稳定面，lora 直接 import `pygent.agent.pygent_v1.runtime.realtime.fork_reasoning` | 可用，但 pygent 改内部路径会断 | 推上游提升到稳定面 |
| SDK 无手动 `compress_session` 入口 | lora `runtime/compression.py` 用 SDK 原语实现（用户拍板） | 维持 |

## 4. 剩余 18 个失败（按桶处理）

运行：`.venv/Scripts/python.exe -m pytest tests/unit -q -p no:cacheprovider`

| 桶 | 用例 | 处理 |
|---|---|---|
| **collaboration 跨界**（并入 B 自研） | `test_agent_collaboration_tools.py` ×2、`test_runtime_service.py::test_runtime_exposes_only_available_agent_collaboration_tools`、`::test_agent_collaboration_uses_pygent_agent_tool_executor`、`test_native_agent_capabilities.py::test_agent_message_is_appended_after_tool_result_without_starting_a_turn`、`test_realtime_scenarios.py` ×2（`AgentMessage.to_dict`） | 现象：`AgentMessage.to_dict` 缺失、`lara.agent.agent_start@1` executor 未注册、`_wait_for_agent_collaborations` 缺失、SDK 暴露 `pygent.v1.scheduler.*` 工具。这些就是 collaboration 自研的 TODO 清单 |
| 测试桩过时 | `test_chat_runner.py::test_deliver_steering_routes_to_interrupt_for_lara_says_session`（`send_interrupt` 新签名无 `execution_id`）、`test_session_manager.py`（桩缺 `sessions_root`）、`test_model_usage_defaults.py`（桩缺 `turn_opening_claim_id`） | 机械修桩 |
| SDK 语义对齐 | `test_trace.py` ×2（`EventStore.append` 现要求 `actor`）、`test_regression_findings.py`、`test_realtime_turn_finalizer.py` ×2、`test_realtime_injection.py::test_speak_call_resets_counter`、`test_session_compression.py`（返回键 `compression_count`）、`test_media_offload.py::test_same_remote_url_downloaded_once` | 逐个对照 SDK 实现改断言或适配器 |

## 5. 下一步（按序）

1. 清第 2、3 桶（约 11 个），全量绿。
2. **collaboration 自研**（B）：在 lora 重写存储 + 工具面（`agent_start/agent_send/agent_wait` 等），接 SDK 的 `ToolSpec`/executor 注册；同时解掉第 1 桶 7 个。SDK 的 `sessions/collaboration.py` 不用。
3. 适配器继续瘦身：`cancel_turn` 可改为 chat_runner 直接用 `ManagedSessionTurn.cancel()`；`send_steering` 与 chat_runner 的 fork-or-turn 分流可整体换成 SDK `LocalExecutionHost.deliver_user_message`（净删除分流代码）。
4. `pyright` 全量（迄今只跑了 pytest）；`tests/scenario` 基线清理（升级前就有 10 个红）。
5. 向 pygent 推上游第 3 节缺口。
6. 可选：数据根是否迁 `~/.pygent_v1`；`examples/minimal_agent` 钉版升级；`docs/` 旧版本号清理。

## 6. 操作要点

- 查副本：`for f in $(find src/lara -name "*.py" | sed 's|src/lara/||'); do [ -f $PYGENT/src/pygent/agent/pygent_v1/$f ] && echo $f; done`（`$PYGENT=E:/Projects/pygent`，本地 pygent 仓与 PyPI 0.6.0 一致）。
- 删副本 → 全仓 `from lara.X import` 改 `from pygent.agent.pygent_v1.X import`（含 `__init__.py` 相对导入、测试里的 monkeypatch 字符串、`import ... as` 形式）。
- 不要用 `git rm --cached`/改 staging 之外的方式动索引；提交推送需用户授权（本轮已授权）。
- 跑全量加 `timeout 900`：若再出现挂死，优先怀疑测试调用了 SDK 已删除的 lora 旧 API（`AttributeError` 后后台任务未清理 → teardown 卡在 `_cancel_all_tasks`）。
