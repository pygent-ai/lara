# Proposal: 会话 / 执行 / 观测三层数据的归属与生命周期 —— pygent 框架层改动清单

> 面向 pygent 维护方。本文给出"应用（lara）怎么用、框架（pygent）怎么改"的完整边界划分与改动清单；
> 对 journal O(n²) 膨胀的量化与复现见已提交的 `pygent-bugreport-journal-quadratic-growth.md`（2026-10-10，基于 0.3.23），
> 本文确认该问题在 **0.6.2 仍原样存在**，并把范围从"journal 体积"扩大到整个数据模型。

## 环境

| 项 | 值 |
|---|---|
| pygent-ai | 0.6.2（`pygent_ai-0.6.2.dist-info`） |
| 应用 | lara（`E:\Projects\lora`，桌面端 + `lara-api`） |
| 数据根 | `C:\Users\Administrator\.lara\projects\<project>\` |
| 实测日期 | 2026-11-17 |

## 1. 现状量化（2026-11-17）

`~/.lara` 总 13.5 GiB，其中：

| 大小 | 内容 |
|---|---|
| **10.6 GiB** | 三个项目的 `.lora/runtime/executions-v1.sqlite3`（6.5 / 2.7 / 1.4 GiB） |
| 1.2 GiB | 一个项目 84 个会话目录 `sessions/` |
| 其余 | < 0.5 GiB |

### 1.1 journal（6.5 GiB 那个）

| 表 | 行数 | JSON 载荷 | 备注 |
|---|---|---|---|
| `events` | 1,695,748 | **5.1 GiB** | 见下 |
| `executions` | 294 | **0.8 GiB** | `input_json`/`output_json` 各 3 MB 级，内嵌完整 Context |
| `effects` | 23,348 | 0.22 GiB | |
| `checkpoints` | **0** | 0 | 图状态检查点表从未被写入 |
| 其余 | — | < 50 MB | |

`freelist_count = 0`：从未删除过任何数据。294 个执行里 6 个 `status='running'` 是崩溃残留（claim 早已过期）。

单个执行样本 `390e3f67…`（一个 turn，succeeded，9,667 个事件，292 MiB）：

| kind | 条数 | 合计 | 单条 |
|---|---|---|---|
| `model.request.prepared` | 186 | **286 MiB** | 1,518 KiB → 1,657 KiB，逐次线性增长 |
| `model.tool_call.delta` | 5,941 | 3.9 MiB | |
| `span.started/completed` | 1,426 | 0.8 MiB | |
| 其余 | — | < 1 MiB | |

同一执行的 `executions.input_json` = 3.1 MB（`context.data.messages` 1,125 条），`output_json` = 3.5 MB。

另一个 10 万事件的执行 `58141670…` 里 75% 行是 `model.reasoning.delta`。

### 1.2 会话目录（84 个会话，1.19 GiB）

| 大小 | 文件 | 性质 |
|---|---|---|
| 227 MiB | `session.json`（84 个，均值 2.7 MB） | `history[]` 全量镜像 |
| 133 MiB | `cases/*/runs/*/events.jsonl` | 含 `conversation.*` 事件 = 消息再拷贝一份 |
| 123 MiB | `context/conversation-checkpoints.sqlite3` | 消息权威源 |
| 114 MiB | `context/history.jsonl` | 消息 append 镜像，运行时只写不读 |
| 114 MiB | `raw-history/events.jsonl` | 仅 eternal memory 用 |
| 110 MiB | `cases/*/runs/*/<源码文件快照>` | file effects 快照 |
| 84 MiB | `cases/*/runs/*/messages.jsonl` | 消息再拷贝一份 |
| 87 MiB | `agent-history/` | 仅 eternal memory 用 |

**同一条对话消息在磁盘上落 5 份**（checkpoints DB、session.json、history.jsonl、run events.jsonl、run messages.jsonl），
再加 journal 里 `input_json` 一份、每次模型调用的 `model.request.prepared` 各一份。

## 2. 恢复链路（已逐行核对）

```
SessionManager.load()                         sessions/manager.py:114
  读 session.json
  _apply_history_checkpoints()                sessions/manager.py:534
    从 conversation-checkpoints.sqlite3 取 sequence > history_checkpoint_seq 的行追加到 session.history
→ restore_session_context()                   runtime/fork_context.py:33
→ _initial_pygent_context()                   graph/common.py:56
    checkpoint = session.metadata["agent_context"]  → context_from_dict()  runtime/codec.py:270
    + _uncovered_history(history, agent_context_history_count)
    若 checkpoint 为 None → 用 history 全量重建
```

**结论：会话恢复只依赖 `session.json`（+ checkpoints DB）。`executions-v1.sqlite3` 不在链路上。**
journal 只在两处被读：

- 进程重启接管 in-flight turn（`orchestration/session_execution.py:139-157`，`recovery_execution_id` 分支）；
- lara 的 Trace 面板读 `model.request.prepared` 全文（lara `src/lara/runtime/model_request_journal.py`）。

## 3. 问题定性

数据按用途分三层，但当前实现把三层混在一起，让观测数据继承了权威数据的永久寿命：

| 层 | 回答的问题 | 应有寿命 | 当前位置 | 问题 |
|---|---|---|---|---|
| **会话层** | 下一轮模型看到什么；用户看到什么 | 与会话同寿 | checkpoints DB（主）+ session.json（镜像）+ 3 份 jsonl 镜像 | 一主四镜像，无单一事实源 |
| **执行层** | turn 跑到哪、崩了怎么接 | 与 turn 同寿，终态后可清 | journal `executions/effects/inputs` | 永不清理；`input_json` 内嵌整个 Context |
| **观测层** | 发给模型什么、工具干了什么 | 由应用保留策略决定 | journal `events`（混在执行层库里）+ 会话目录 `cases/`、`logs/` | 与执行层同库同寿；每次模型调用全量重写 |

另有一个独立但同源的问题（§4.6）：Context codec 的 identity 严格匹配，schema 改名即令存量会话不可加载。

## 4. 改动清单

按优先级排序。每项给出：现状（代码位置）、改法、验收。

### 4.1 [P0] journal 终态保留策略

**现状**
- `runtime/_history_executions.py:433 delete_execution(execution_id)` 存在，但整个代码库无调用者。
- 无 `auto_vacuum`，无 `incremental_vacuum`。
- `running` 残留无回收路径（claim 过期后执行行永远停在 `running`）。

**改法**
```python
class ExecutionHistory:
    async def purge_terminal_executions(
        self, *, before_unix_ns: int, keep_summary: bool = True,
        batch_size: int = 100,
    ) -> PurgeReport:
        """删除 updated_at < before 且 status 为终态的执行的 events/effects/
        execution_inputs/execution_input_receives/execution_input_consumers/
        execution_inboxes/execution_model_admissions。
        keep_summary=True 时保留 executions 行（input_json/output_json 置 NULL）。"""

    async def reap_abandoned_executions(self, *, now: float) -> int:
        """status='running' 且 execution_claims.expires_at < now 的执行
        标为 status='abandoned'、phase='terminal'，使其进入可清范围。"""
```
- 打开库时 `PRAGMA auto_vacuum = INCREMENTAL`（新库）；对存量库提供一次性 `VACUUM` 迁移入口。
- `purge_terminal_executions` 结束后执行 `PRAGMA incremental_vacuum`。
- `RuntimeDurabilityConfig`（`agent/pygent_v1/schema/models.py:76`）增加：
  ```python
  retention_seconds: int | None = None   # None = 不自动清理（保持现行为）
  reap_on_open: bool = True
  ```
  非 None 时在 runtime 打开 journal 时跑一次 reap + purge。

**验收**
- 对本现场库执行 `purge_terminal_executions(before=now-7d)` 后，文件体积 ≤ 300 MB；
- 清理后 in-flight 执行的 `recover_turn` 行为不变（用一个 running 执行做重启恢复测试）；
- `freelist_count` 在 purge + incremental_vacuum 后归零。

### 4.2 [P0] 观测事件与执行账本分离：`EventSink` 扩展点

**现状**
- 所有 `ModelEventKind` 事件和 span/effect/tool 事件都经 `_history_executions.py:643/713` 写入同一张 `events` 表。
- `llm/invoker.py:607-615`：每个 attempt 循环内 `_emit(event_sink, REQUEST_PREPARED, prepared_event)`，`prepared_event` 含完整 `messages`/`tools`/`system_prompt`。
- `Binding` 层只有 `DurabilityPolicy(mode)`（`runtime/api.py:132`），无法指定"哪些事件需要持久化"。

**改法**
1. 把事件分成两类，在 `ModelEventKind` / runtime event kind 上打标：
   - `DURABLE`：`execution.*`、`effect.executed`、`tool.requested/authorized/...`、`span.*`、`model.attempt.*`、`model.*.completed` —— 恢复/审计必需，写 `events` 表；
   - `OBSERVABLE`：`model.request.prepared`、`model.reasoning.delta`、`model.text.delta`、`model.tool_call.delta` —— 默认**不写** `events` 表。
2. `DurabilityPolicy` 增加：
   ```python
   @dataclass(frozen=True, slots=True)
   class DurabilityPolicy:
       mode: DurabilityMode = DurabilityMode.PREFERRED
       persist_observable_events: bool = False      # 兼容开关；True = 现行为
       observable_sink: EventSink | None = None     # 应用自带的落地点
   ```
   ```python
   class EventSink(Protocol):
       async def emit(self, execution_id: str, event: RuntimeEvent) -> None: ...
   ```
   `OBSERVABLE` 事件：有 sink 则交给 sink；无 sink 且 `persist_observable_events=False` 则只进内存事件流（SSE 订阅者仍能收到），不落盘。
3. 流式 delta 在模型调用结束时由 invoker 折叠为一条 `model.reasoning.completed` / `model.text.completed`（`DURABLE`，含最终文本和 usage），这样终态审计不依赖 delta。

**验收**
- 默认配置下跑一个 50 次模型调用的 turn，`events` 表增量 < 2 MB；
- 配置 `observable_sink` 后，sink 收到 50 条 `model.request.prepared`，`events` 表里 0 条；
- SSE 流上的 delta 事件不受影响（lara 前端流式显示无变化）。

### 4.3 [P1] `model.request.prepared` 按前缀引用 + 增量

**现状**
`llm/_request_snapshot.py:_request_projection` 每次整份序列化；同一执行内相邻调用的 `messages` 互为前缀，差量只在尾部。`request_digest` 已计算但只作记录。

**改法**
- 事件载荷改为：
  ```json
  {
    "request_digest": "sha256:…",
    "base_ref": {"execution_id": "…", "event_index": 123, "message_count": 1100} | null,
    "messages_appended": [ …仅新增的消息… ],
    "messages_truncated": 0,
    "projection_revision": 7,
    "system_prompt_digest": "sha256:…",
    "tools_digest": "sha256:…",
    "generation": {…}
  }
  ```
- 执行内首次调用或 `projection_revision` 变化（上下文压缩）时 `base_ref=null` 存全量；之后存增量。
- 同一 `request_digest` 的重试 attempt 只存 `{"request_digest", "attempt", "same_as_attempt": 1}`。
- 提供 `ExecutionHistory.resolve_request(execution_id, event_index) -> PreparedRequest` 把链展开，供消费方（Trace 面板）使用。

**验收**
- 样本执行 `390e3f67…`（186 次调用）重放后 `model.request.prepared` 总量 < 10 MiB（现 286 MiB）；
- `resolve_request` 展开结果与旧格式全量快照逐字段相等。

### 4.4 [P1] `executions.input_json / output_json` 存引用

**现状**
`input_json` = `{"context": <完整 Context 编码>, "message": …}`，3 MB 级；`output_json` 同量级。而 Context 已由会话层（checkpoints DB + `agent_context`）完整描述。

**改法**
- `input_json` 存 `ContextRef`：
  ```json
  {"context_ref": {"codec": "pygent.v1.agent-context", "version": 6,
                   "session_id": "…", "history_checkpoint_seq": 731,
                   "agent_context_digest": "sha256:…"},
   "message": …}
  ```
- `Binding` 注册 `ContextResolver`，recovery 时由应用把 ref 解析回 Context（lara 侧实现就是 `restore_session_context`）。
- 无 resolver 的 binding 保持现行为（存全量），保证 SDK 独立可用。
- 这是 schema 变更，建议随 `executions-v2` 一起做；`checkpoints` 表（0 行、无写入点）可借机移除或明确其用途。

**验收**
- 配置 resolver 后，`executions` 行均长 < 20 KB；
- 重启恢复测试通过。

### 4.5 [P2] 会话层：checkpoints DB 成为唯一主，镜像降级为可选 sink

**现状**
- `sessions/manager.py:534 _apply_history_checkpoints`：`load()` 把 DB 新行追加进 `session.history`，`save()`（`:445`）把 `history[]` 整份写回 `session.json`。两者是"双写"而非"主从"：`history_checkpoint_seq` 游标一旦与 DB 不一致就会重复追加或丢失。
- `graph/pipeline.py:88-200 checkpoint_conversation_message`：写 DB 后再 `store.append("conversation.*", payload=message)` 写 `cases/<run>/events.jsonl`。
- `tracing/events.py:67/170`：再写 `cases/<run>/messages.jsonl` 与 `context/history.jsonl`。
- lara 的 UI 已经直接读 DB（`history_with_run_timing`），不读 `session.json.history`。

**改法**
- `AgentSession.history` 变为惰性投影：`load()` 不再物化；需要时 `manager.history(session, limit=…)` 从 DB 读。
- `session.json` 只保留：`system_prompt`、模型选择、`metadata.agent_context`、`history_checkpoint_seq`、时间戳。
- `conversation.*` 事件、`messages.jsonl`、`history.jsonl` 三处写入改为 `TracingSink` 的可选行为（默认关），由应用决定是否开启。
- 兼容：旧 `session.json` 含 `history[]` 时，`load()` 用其内容与 DB 做一次对账（以 DB 为准）并写回精简格式。

**验收**
- 84 个会话迁移后 `session.json` 总量 < 5 MB（现 227 MB）；
- `restore_session_context` 输出与迁移前逐条相等；
- 删除 `context/history.jsonl`、`cases/*/runs/*/{events,messages}.jsonl` 后会话恢复与 UI 显示均不受影响。

### 4.6 [P1] Context codec identity 需要迁移机制

**现状**
- `runtime/codec.py:270 context_from_dict` 按 `(schema, version, codec, codec_digest)` 四元组精确匹配，任一不同即 `WireCodecError("invalid Context")`。
- 0.6.x 把 schema 从 `lara.agent-context` 改名为 `pygent.v1.agent-context`（字段完全相同，digest 因含 schema 名而变化），导致本机 **100 个**存量会话的 `metadata.agent_context` 全部不可加载，turn 直接 `error`。
- 调用方 `graph/common.py:77` 对这个异常没有降级（checkpoint 损坏时本可退回 `history` 全量重建，但异常直接冒到 turn 失败）。

**改法**
- `ContextCodecRegistry` 支持别名/迁移注册：
  ```python
  registry.register_migration(
      from_identity=("lara.agent-context", 6, "pygent-dataclass-json-v1", "sha256:b937…"),
      to=codec_v6,            # 字段相同，直接重解释
      transform=None,         # 或给 data -> data 的升级函数
  )
  ```
- `_initial_pygent_context` 对 `WireCodecError` 做降级：记录 warning，`checkpoint=None` 走 `history` 全量重建，并在本 turn 结束时写回新格式 checkpoint。
- 版本升级时附带迁移脚本或在 `load()` 时惰性迁移。

**验收**
- 用改名前的 `session.json` 直接跑 turn 成功，且 turn 结束后 `agent_context.schema` 已更新为新名；
- 人为破坏 `agent_context.data` 后 turn 仍能完成（降级路径生效，日志有 warning）。

## 5. 应用侧（lara）配套，供对齐

这些不需要 pygent 做，列出来是为了说明接口如何被消费：

| pygent 项 | lara 对接 |
|---|---|
| 4.1 | `lara runtime prune` 命令 + scheduler 每日任务；`runtime.durability.retention_days` 配置透传 |
| 4.2 | 实现 `EventSink`，把 `model.request.prepared` 写到会话目录 `logs/model_requests.jsonl`，随 lara 自己的会话清理策略一起生命周期管理；`ModelRequestJournal` 改读该文件 |
| 4.3 | Trace 面板改调 `resolve_request` |
| 4.4 | 注册 `ContextResolver = restore_session_context` |
| 4.5 | `session_service._first_user_message_*` 三级兜底砍为读 DB 一级 |
| 4.6 | 删除 lara 侧手工修补 `session.json` 的脚本 |

在 4.2 落地前，lara 会先接受"Trace 面板只能看未被 prune 的 turn"作为过渡。

## 6. 实施顺序建议

```
0.6.x patch   4.1 终态清理 + 4.6 codec 迁移         （无 schema 变更，纯增量，立即止血）
0.7           4.2 EventSink + delta 折叠            （DurabilityPolicy 增字段，默认行为改变需 release note）
0.7           4.3 request.prepared 增量链
0.8           4.4 executions-v2 存引用 + 4.5 会话层单主
```

4.1 与 4.6 合计改动预计 < 300 行，建议先出。

## 7. 复现脚本

任一长期使用的 journal：

```sql
PRAGMA freelist_count;
SELECT status, COUNT(*) FROM executions GROUP BY 1;
SELECT json_extract(event_json,'$.kind') k, COUNT(*), SUM(LENGTH(event_json))/1048576.0 MiB
  FROM events GROUP BY 1 ORDER BY 3 DESC LIMIT 10;
SELECT execution_id, COUNT(*), SUM(LENGTH(event_json))/1048576.0 MiB
  FROM events GROUP BY 1 ORDER BY 3 DESC LIMIT 5;
SELECT AVG(LENGTH(input_json))/1024, AVG(LENGTH(COALESCE(output_json,'')))/1024 FROM executions;
```

codec 问题复现：

```python
from pygent.runtime.codec import context_from_dict
from pygent.agent.pygent_v1.runtime.context import PYGENT_CONTEXT_CODECS
ck = json.load(open("session.json"))["metadata"]["agent_context"]   # schema == "lara.agent-context"
context_from_dict(ck, registry=PYGENT_CONTEXT_CODECS)
# WireCodecError: invalid Context  <- ContextCodecError: unknown or incompatible Context codec
```
