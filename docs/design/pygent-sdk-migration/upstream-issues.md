# pygent 0.6.0 上游问题报告（来自 lara 迁移）

> 整理日期：2026-11-14。基线：本地 `C:/Projects/pygent` **0.6.2**（`71f2ea0`）。#1、#4 在 0.6.1 修复；#2、#3、#5、#6、#7 在 0.6.2 修复（lara 侧的临时处理已随之删除）。仍开放：#8、#9。
> 每一项都给出 lara 侧的临时处理和"上游修复后 lara 可删什么"，便于回收代码。

## 1. ~~`AgentMessage` 缺 `to_dict`~~（0.6.1 已随协作收件箱整体删除）

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

## 2. ~~`_child_agent_config` 用 SDK `RunConfig.from_dict`~~（0.6.2 已修：`type(self.config).from_dict`）

- **位置**：`pygent/agent/pygent_v1/runtime/service.py:638-641`
  ```python
  if alias == self.config.agent_alias:
      return RunConfig.from_dict(self.config.to_dict())
  ```
- **现象**：宿主把 `RunConfig` 子类化并扩展嵌套配置（lara：`DelegationConfig.allowed_agents/max_parallel/background_enabled`）后，`self.config.to_dict()` 带出扩展键，SDK 的 `RunConfig.from_dict` → SDK `DelegationConfig(**value)` 抛 `TypeError: DelegationConfig.__init__() got an unexpected keyword argument 'allowed_agents'`。`agent_start` 等需要子配置的路径全部不可用。
- **建议修复**：`type(self.config).from_dict(self.config.to_dict())`，或直接 `copy.deepcopy(self.config)`；更进一步，`run_config_loader` 注入了就统一走它，不再有同 alias 的特殊分支。
- **lara 临时处理**：`LaraRuntimeService._child_agent_config` 覆盖。上游修复后可删该方法并改为注入 `run_config_loader=load_run_config`。

## 3. ~~`FileEffectTracker.IGNORED_DIRS` 硬编码 `.pygent_v1`~~（0.6.2 已修：`RunConfig.workspace_runtime_dir` → `root_ignored_dirs`）

- **位置**：`pygent/agent/pygent_v1/runtime/tools.py:36-40`
- **现象**：宿主的数据目录不叫 `.pygent_v1`（lara 用 `.lara`，由 `RunConfig.user_pygent_root/pygent_root` 注入）时，运行时自己写的文件（sessions、索引、sqlite）被当成 agent 的文件效应记录。
- **建议修复**：从 `RunConfig` 推导：把 `Path(config.pygent_root).name`（以及 `user_pygent_root`）并入忽略集合；或在 `RunConfig` 上暴露 `file_effect_ignored_dirs`。
- **lara 侧**：`tests/unit/test_tools.py::test_file_effect_tracker_ignores_runtime_directories` 标 `xfail(strict)`，修复后去掉标记即可。

## 4. ~~`offload_remote_media` 重复下载~~（0.6.1 已修：`fetched` 提到入口作用域）

- **位置**：`pygent/agent/pygent_v1/graph/media_offload.py:114-115`
  ```python
  def handle(node):
      return _offload_remote_node(node, root, fetcher, {})
  ```
- **现象**：docstring 承诺 "downloaded once"，实际每个引用同一 URL 的节点各下载一次。
- **建议修复**：把 `fetched: dict[str, bytes | None] = {}` 提到 `offload_remote_media` 作用域，`handle` 闭包复用。
- **lara 侧**：`tests/unit/test_media_offload.py::test_same_remote_url_downloaded_once` 标 `xfail(strict)`。

## 5. ~~`fork_model_profiles` 不在稳定面~~（0.6.2 已修：`pygent.agent.pygent_v1.runtime` 导出）

- **位置**：`pygent/agent/pygent_v1/runtime/realtime/fork_reasoning.py`
- **现象**：lara 的 fork 回复要按会话模型组选 fork profile，只能 import 这个内部路径；pygent 调整内部布局会直接断。
- **建议**：提升到 `pygent.agent.pygent_v1.runtime`（或 `pygent.agent`）的公开导出。

## 6. ~~`media_offload.UPLOADS_SUBDIR` 硬编码~~（0.6.2 已修：`RunConfig.workspace_runtime_dir` + `uploads_dir` 参数；lara 设 `.lara`，`.gitignore` 的 `.pygent_v1/` 已删）

- **位置**：`pygent/agent/pygent_v1/graph/media_offload.py:30`
  ```python
  UPLOADS_SUBDIR = Path(".pygent_v1") / "uploads"
  ```
- **现象**：模型输出里的 inline/远程媒体被落盘到用户项目目录下的 `.pygent_v1/uploads/`。宿主数据目录叫别的名字时（lara：`.lara/`，`lara_api/services/uploads.py` 把用户上传放在 `.lara/uploads`），项目里会同时出现两个运行时目录，而且 `.pygent_v1/` 不在宿主的 `.gitignore` 里，会污染用户仓库。与第 3 项同根：工作区内的运行时目录名应来自 `RunConfig`。
- **建议修复**：`offload_inline_media`/`offload_remote_media` 接受 `uploads_dir`（或从 `config.pygent_root` 推导目录名），默认值保持 `.pygent_v1/uploads`。
- **lara 临时处理**：`.gitignore` 加 `.pygent_v1/`。上游修复后改回 `.lara/uploads` 并删掉这条 ignore。

## 7. ~~SDK 工具注册没有"宿主扩展点"~~（0.6.2 已修：`speak_channels` / `external_tools` 构造参数；lara 用 `speak_channels` 挂 Feishu）

不算 bug，但迁移中反复碰到：`PygentRuntimeService.__init__` 固定注册 speak（`channels=()`）、scheduler 工具，并用这组工具构建模板 agent。宿主要换 speak 通道或改可见工具集，只能 `register_tool(..., replace_existing=True)` 再清空 `_agent_definitions` 重建。若 `__init__` 接受 `speak_channels` / `external_tools` 两个可选参数，lara 的 `LaraRuntimeService.__init__` 可再删十几行。

## 8. `SessionManager.create` 不接受指定 `session_id`（API 面）

- **位置**：`pygent/agent/pygent_v1/sessions/manager.py` `create()`：id 只能由 `_new_session_id` 生成，唯一例外是 `pygent.v1-says` 模式。
- **现象**：调度器任务用稳定的会话名（`SchedulerConfig.session`，daemon 执行 `session run <task.session>`），宿主没法用该名字创建会话；`load_or_create(SessionSpec(session_id=...))` 在 `new` 模式下也忽略 `session_id`。
- **建议修复**：`create(..., session_id: str | None = None)`，给定时校验 `validate_path_id` 并直接使用。
- **lara 临时处理**：`SessionManager.ensure(session_id)` 用实例属性覆盖 `_new_session_id` 后调 SDK `create`。上游修复后改为一行 `create(case_id, mode="chat", session_id=...)`。

## 9. `realtime_enabled_for` 只认 `pygent.v1-says`（产品需求）

- **位置**：`pygent/agent/pygent_v1/sessions/manager.py:35`
- **现象**：`deliver_user_message` 只对这一个会话走 fork 路径；lara 希望将来所有会话在运行中收到用户消息都 fork 回复。
- **建议**：让 realtime 判定可配置（`RunConfig.realtime_interaction` 已有 `enabled`，但 0.6.x 改成了按会话模式门控），例如 `realtime_interaction.sessions: "says" | "all"`。
