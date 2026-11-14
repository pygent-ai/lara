# pygent 0.6.0 上游问题报告（来自 lara 迁移）

> 整理日期：2026-11-14。基线：PyPI `pygent-ai==0.6.0`（`.venv/Lib/site-packages/pygent`）。
> 每一项都给出 lara 侧的临时处理和"上游修复后 lara 可删什么"，便于回收代码。

## 1. `AgentMessage` 缺 `to_dict`，但 pipeline 会调用它（bug）

- **位置**：`pygent/agent/pygent_v1/graph/pipeline.py:714`
  ```python
  "agent_messages": [item.to_dict(include_content=True) for item in agent_messages],
  ```
  `agent_messages` 来自 `ReminderService.claim_agent_messages()` → `SessionCollaborationStore.claim_messages()` → `AgentMessage.from_dict(...)`。
- **现象**：`pygent/agent/pygent_v1/sessions/collaboration.py:22` 的 `AgentMessage` 只有 `from_dict`，没有 `to_dict`。任何会话一旦收件箱里有 Agent 消息（`agent_send`、fork 回复信封），tool 边界就抛 `AttributeError: 'AgentMessage' object has no attribute 'to_dict'`，整轮失败。
- **复现**：`tests/unit/test_native_agent_capabilities.py::test_agent_message_is_appended_after_tool_result_without_starting_a_turn`（去掉 lara 补丁后）。
- **建议修复**：给 `AgentMessage` 加
  ```python
  def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
      value = {"message_id": ..., "submission_id": ..., "source_session_id": ..., "source_agent_alias": ...,
               "target_session_id": ..., "status": self.state.value, "created_at": ..., "updated_at": ...,
               "delivered_at": ..., "delivered_execution_id": ...}
      if include_content: value["content"] = self.content
      return value
  ```
  （与 `from_dict` 的键一一对应，`status` 对应 `state`。）
- **lara 临时处理**：`src/lara/sessions/collaboration.py` 对 SDK 类打 monkeypatch。**上游修复后可整删该文件**，`lara.sessions` 直接 re-export SDK。

## 2. `PygentRuntimeService._child_agent_config` 用 SDK `RunConfig.from_dict` 复制配置，拒绝宿主扩展字段（扩展性缺口）

- **位置**：`pygent/agent/pygent_v1/runtime/service.py:638-641`
  ```python
  if alias == self.config.agent_alias:
      return RunConfig.from_dict(self.config.to_dict())
  ```
- **现象**：宿主把 `RunConfig` 子类化并扩展嵌套配置（lara：`DelegationConfig.allowed_agents/max_parallel/background_enabled`）后，`self.config.to_dict()` 带出扩展键，SDK 的 `RunConfig.from_dict` → SDK `DelegationConfig(**value)` 抛 `TypeError: DelegationConfig.__init__() got an unexpected keyword argument 'allowed_agents'`。`agent_start` 等需要子配置的路径全部不可用。
- **建议修复**：`type(self.config).from_dict(self.config.to_dict())`，或直接 `copy.deepcopy(self.config)`；更进一步，`run_config_loader` 注入了就统一走它，不再有同 alias 的特殊分支。
- **lara 临时处理**：`LaraRuntimeService._child_agent_config` 覆盖。上游修复后可删该方法并改为注入 `run_config_loader=load_run_config`。

## 3. `FileEffectTracker.IGNORED_DIRS` 硬编码 `.pygent_v1`（配置缺口）

- **位置**：`pygent/agent/pygent_v1/runtime/tools.py:36-40`
- **现象**：宿主的数据目录不叫 `.pygent_v1`（lara 用 `.lara`，由 `RunConfig.user_pygent_root/pygent_root` 注入）时，运行时自己写的文件（sessions、索引、sqlite）被当成 agent 的文件效应记录。
- **建议修复**：从 `RunConfig` 推导：把 `Path(config.pygent_root).name`（以及 `user_pygent_root`）并入忽略集合；或在 `RunConfig` 上暴露 `file_effect_ignored_dirs`。
- **lara 侧**：`tests/unit/test_tools.py::test_file_effect_tracker_ignores_runtime_directories` 标 `xfail(strict)`，修复后去掉标记即可。

## 4. `offload_remote_media` 对每个节点传新 `fetched={}`，同一 URL 重复下载（bug）

- **位置**：`pygent/agent/pygent_v1/graph/media_offload.py:114-115`
  ```python
  def handle(node):
      return _offload_remote_node(node, root, fetcher, {})
  ```
- **现象**：docstring 承诺 "downloaded once"，实际每个引用同一 URL 的节点各下载一次。
- **建议修复**：把 `fetched: dict[str, bytes | None] = {}` 提到 `offload_remote_media` 作用域，`handle` 闭包复用。
- **lara 侧**：`tests/unit/test_media_offload.py::test_same_remote_url_downloaded_once` 标 `xfail(strict)`。

## 5. `fork_model_profiles` 不在稳定面（API 面）

- **位置**：`pygent/agent/pygent_v1/runtime/realtime/fork_reasoning.py`
- **现象**：lara 的 fork 回复要按会话模型组选 fork profile，只能 import 这个内部路径；pygent 调整内部布局会直接断。
- **建议**：提升到 `pygent.agent.pygent_v1.runtime`（或 `pygent.agent`）的公开导出。

## 6. 同类观察：SDK 工具注册没有"宿主扩展点"

不算 bug，但迁移中反复碰到：`PygentRuntimeService.__init__` 固定注册 speak（`channels=()`）、scheduler 工具，并用这组工具构建模板 agent。宿主要换 speak 通道或改可见工具集，只能 `register_tool(..., replace_existing=True)` 再清空 `_agent_definitions` 重建。若 `__init__` 接受 `speak_channels` / `external_tools` 两个可选参数，lara 的 `LaraRuntimeService.__init__` 可再删十几行。
