# Bug Report: 多 Session 共享项目级 SQLite Journal，跨会话锁竞争可使单个 Turn 瞬时失败

## 摘要

Lara 当前把同一项目下所有 session 的 Pygent execution 写入同一个项目级 SQLite journal：

```text
~/.lara/projects/<project-id>/.lora/runtime/executions-v1.sqlite3
```

数据库使用 WAL，但 SQLite 仍然只有一个 writer。一个长会话持续写入 execution、event、effect、tool task 和 checkpoint 时，其他会话的新建、恢复、模型阶段切换或工具调度也会竞争同一把写锁。

Pygent 0.3.25 已为 `SQLiteHistoryStore` 和 `SQLiteModelDeploymentStore` 配置 30 秒 `busy_timeout`，但写路径没有对 `SQLITE_BUSY`、`SQLITE_BUSY_SNAPSHOT` 或 `SQLITE_LOCKED` 做事务级 rollback + 有界重试。因此，不能由 busy handler 等待的瞬时锁错误仍会原样冒泡为：

```text
sqlite3.OperationalError: database is locked
```

在 Lara 上层，它被记录为 `runtime.error`，随后整个 turn 被标记为 `error`。`model.response` 中虽然也显示错误，但这不是模型提供商返回的错误。

## 环境

| 项 | 值 |
|---|---|
| Lara | 当前仓库工作区 |
| pygent-ai | 0.3.25（PyPI） |
| aiosqlite | 0.22.1 |
| Python | 3.13.3 |
| SQLite | 3.49.1 |
| 平台 | Windows，journal 位于本地 NTFS |
| journal 模式 | WAL |
| `busy_timeout` | history store 与 deployment store 均为 30 秒 |
| 项目 journal 实测体积 | 2,079,199,232 字节（约 1.94 GiB） |
| WAL 实测体积 | 4,338,392 字节（约 4.14 MiB） |
| `wal_autocheckpoint` | 1000 pages（4 KiB page 时约 4 MiB） |
| `freelist_count` | 0，不是可由普通 `VACUUM` 回收的空洞 |

## 生产历史证据（2026-10-13）

在同一个 Lara 项目中，共找到 5 个受影响 run、6 条真实 `runtime.error: database is locked` 事件：

| 本地时间 | run | 事件数 | run 最终状态 | 报错时有其他 run 活跃 |
|---|---|---:|---|---|
| 15:56:37 | `run-20261013-155556-618368-6b5e9b` | 1 | error | 是 |
| 15:57:55 | `run-20261013-155711-490052-553fe7` | 2 | passed | 是 |
| 15:58:44 | `run-20261013-115518-954046-02f5ce` | 1 | passed | 是（2 个） |
| 16:07:32 | `run-20261013-160556-933954-e18c98` | 1 | error | 是 |
| 16:18:25 | `run-20261013-153759-040055-1414a5` | 1 | error | 否 |

6 条错误中有 5 条发生在另一个 run 同时活跃期间。最后一条属于此前持续约 40 分钟的长 run 自身终结阶段，说明跨 session 并发是强放大因素，但同一 execution 内不同写路径或连接的竞争也可能触发问题。

### 本次事故时间线

会话：`chat-chat-20261013-155556-ed5b76`  
run：`run-20261013-160556-933954-e18c98`  
并发长 run：`run-20261013-153759-040055-1414a5`（15:37:59–16:18:25）

| 本地时间 | 事件 |
|---|---|
| 16:07:32.142 | 模型回复已持久化，并生成两个工具调用 |
| 16:07:32.251 | `runtime.file_scan.incomplete`（byte budget exceeded） |
| 16:07:32.339 | `runtime.error: database is locked` |
| 16:07:32.398 | `model.response status=error`，`model_id/provider` 均为 null |
| 16:07:32.399 | `context.checkpoint status=error`，run 终态为 error |

错误距上一个运行时事件约 88 ms，距助手消息持久化约 197 ms，明显短于 30 秒 `busy_timeout`。这与 `SQLITE_BUSY_SNAPSHOT` 或 `SQLITE_LOCKED` 等不调用 busy handler 的立即失败类别相符。

相邻的 `runtime.file_scan.incomplete` 只表示文件扫描达到字节预算，不是数据库锁的根因。

## 复现 A：稳定复现“30 秒 timeout 仍立即失败”

下面的两个 SQLite 连接分别模拟两个 session。它们共享同一 WAL 数据库：session A 先建立读快照，session B 在该快照之后提交写入，随后 session A 尝试把旧读事务升级为写事务。

在 SQLite 中，这会稳定触发 `SQLITE_BUSY_SNAPSHOT`；busy handler 无法通过等待解决，因为 session A 的快照已经过期，必须 rollback 后重新开始事务。

```python
import sqlite3
import tempfile
import time
from pathlib import Path

db = Path(tempfile.mkdtemp()) / "shared-session-journal.sqlite3"

# 两个连接模拟两个 session；都配置 30 秒 timeout。
session_a = sqlite3.connect(db, timeout=30.0, isolation_level=None)
session_b = sqlite3.connect(db, timeout=30.0, isolation_level=None)

session_a.execute("PRAGMA journal_mode=WAL")
session_a.execute("PRAGMA busy_timeout=30000")
session_b.execute("PRAGMA journal_mode=WAL")
session_b.execute("PRAGMA busy_timeout=30000")
session_a.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, payload TEXT)")

# A 建立 deferred 读事务和 WAL 快照。
session_a.execute("BEGIN")
session_a.execute("SELECT count(*) FROM events").fetchone()

# B 在 A 的快照之后成功提交一次写入。
session_b.execute("BEGIN IMMEDIATE")
session_b.execute("INSERT INTO events(payload) VALUES ('written by session B')")
session_b.execute("COMMIT")

# A 尝试把旧读事务升级为写事务；不能靠等待恢复。
started = time.monotonic()
try:
    session_a.execute(
        "INSERT INTO events(payload) VALUES ('written by session A')"
    )
    session_a.execute("COMMIT")
    print("unexpected: write succeeded")
except sqlite3.OperationalError as exc:
    elapsed = time.monotonic() - started
    print(f"error={exc}")
    print(f"sqlite_errorcode={getattr(exc, 'sqlite_errorcode', None)}")
    print(f"sqlite_errorname={getattr(exc, 'sqlite_errorname', None)}")
    print(f"elapsed={elapsed:.3f}s (busy_timeout=30s)")
    session_a.execute("ROLLBACK")
finally:
    session_a.close()
    session_b.close()
```

运行：

```powershell
uv run python .\repro_shared_session_lock.py
```

预期输出：

```text
error=database is locked
sqlite_errorcode=517
sqlite_errorname=SQLITE_BUSY_SNAPSHOT
elapsed=0.000s (busy_timeout=30s)
```

关键判据不是必须精确为 `0.000s`，而是错误远早于 30 秒返回，且错误码为 `SQLITE_BUSY_SNAPSHOT`。这证明单纯继续调大 `busy_timeout` 不能修复该类故障。

## 复现 B：Lara 多 Session 集成复现

此复现依赖写入时序，不像复现 A 那样每次必现；journal 越大、长会话写入越频繁，命中概率越高。

### 前置条件

1. 使用同一个 Lara project，不要创建第二个 project。
2. 确认两个 session 的 runtime history path 都解析到同一个：

   ```text
   ~/.lara/projects/<project-id>/.lora/runtime/executions-v1.sqlite3
   ```

3. 使用 Pygent 0.3.25，并保留默认 WAL 与 30 秒 timeout。
4. 为避免污染正式数据，建议复制工作区并使用独立的临时 Lara home。

### 操作步骤

1. 在 session A 启动一个长任务，使其持续进行模型调用和工具调用，例如扫描较大仓库、连续分析多批文件并持续输出阶段结果。
2. 确认 session A 仍处于 running 状态，并且 `executions-v1.sqlite3-wal` 的修改时间持续变化。
3. 在同一 project 中新建 session B，发送一个会触发“模型回复 → 多工具调用”的请求。
4. 在 A 运行期间，反复执行以下任一操作以提高竞争概率：
   - 在 B 中继续发送需要工具调用的消息；
   - 新建 session C 并立即发送消息；
   - 在已有 session 间切换并恢复尚未结束的 execution。
5. 检查各 run 的 `events.jsonl`：

   ```powershell
   rg -n 'database is locked|runtime.error' `
     "$env:USERPROFILE\.lara\projects\<project-id>\sessions" `
     -g events.jsonl
   ```

6. 检查命中错误时是否存在另一个时间区间重叠的 `run_metadata.json`，并记录两个 run 的开始、结束时间。

### 复现成功判据

满足以下全部条件即可判定复现成功：

1. session B/C 的 `events.jsonl` 出现：

   ```json
   {"type":"runtime.error","payload":{"error":"database is locked","error_type":"OperationalError"}}
   ```

2. 同一 run 随后出现 `model.response status=error` 与 `context.checkpoint status=error`，或者虽然后续恢复，但至少记录了一次锁错误。
3. 错误发生时另一个 session 的 run 仍处于活跃区间，或同一长 run 正在并发执行不同持久化路径。
4. 错误在远小于 30 秒的时间内返回；若能插桩，应记录扩展错误码是否为 `SQLITE_BUSY_SNAPSHOT`、`SQLITE_BUSY` 或 `SQLITE_LOCKED`。

## 为什么“所有 Session 共用一个数据库”会放大问题

WAL 解决的是 reader 与 writer 的大部分互相阻塞问题，并没有把 SQLite 变成多 writer 数据库。同一个项目中：

```text
session A ─┐
session B ─┼─> executions-v1.sqlite3 ─> 单一 WAL writer / checkpoint
session C ─┘
```

因此会产生以下跨会话影响：

1. 所有 session 的写事务最终仍需串行获取 WAL 写锁；
2. 大请求快照、事件批量 flush 或 checkpoint 会延长持锁尾部；
3. WAL 达到默认 1000 pages 时触发的 checkpoint 成本由整个项目共同承担；
4. 一个 session 的 journal 写放大会增加其他 session 的延迟和失败概率；
5. 单次锁异常没有被 store 层吸收时，故障边界从“一次数据库写入”扩大为“整个 turn 失败”。

## 当前实现核对

Pygent 0.3.25 已包含以下等待配置：

- `SQLiteHistoryStore(timeout_seconds=30.0)`；
- `aiosqlite.connect(..., timeout=self._timeout_seconds)`；
- `PRAGMA busy_timeout=30000`；
- `SQLiteModelDeploymentStore` 使用相同的 30 秒配置。

但在 `pygent/runtime` 中仍找不到针对锁类 `OperationalError` 的有界重试。多个写路径直接执行 `BEGIN IMMEDIATE`，锁错误会穿透到 Lara。

当前错误也不携带数据库路径、表名、事务阶段或 SQLite 扩展错误码，所以生产记录无法最终指认失败来自 history store、deployment store，还是具体哪条写语句。

## 期望行为与修复验收

### 必需修复

1. 对可重试的 `SQLITE_BUSY`、`SQLITE_BUSY_SNAPSHOT` 和 `SQLITE_LOCKED` 做有界退避重试；
2. 每次重试前必须 rollback 并重新建立事务/快照，不能在失效事务上原地重试；
3. 上抛最终错误时记录 DB 路径、store、操作名、事务阶段、等待时长、attempt 和 SQLite 扩展错误码；
4. 同一项目下一个 session 的瞬时写冲突不能直接终止另一个 session 的 turn。

### 架构缓解

1. execution journal 按 session 分片，例如：

   ```text
   runtime/sessions/<session-id>/executions.sqlite3
   ```

2. 项目级容量协调、模型部署索引等真正需要全局一致性的少量数据继续使用项目级数据库；
3. 如果暂不分库，至少通过项目级单 writer 队列统一落盘，避免同一进程内多个连接直接争抢 writer；
4. 对终态 execution 的 delta 和重复全量请求快照建立压缩、去重或保留策略，降低 journal 与 checkpoint 压力。

### 回归验收

1. 复现 A 必须由 store 层 rollback + retry 后成功，而不是把错误上抛给调用方；
2. 在同一项目并行运行至少两个高频写 session，连续执行足够多轮，不出现 terminal `database is locked`；
3. 注入持锁、`SQLITE_BUSY_SNAPSHOT` 和 `SQLITE_LOCKED` 故障，验证重试次数、退避上限和错误上下文；
4. 验证进程崩溃、取消、恢复和 fencing 语义没有因重试或分库而退化；
5. 记录 WAL 峰值、checkpoint 时长、写事务 P95/P99 和重试次数，确认修复不是仅把失败转换成不可接受的长时间卡顿。

## 与现有报告的关系

- `pygent-bugreport-busy-timeout.md`：定位 history store 缓慢写锁竞争依赖 SQLite 默认 5 秒 timeout；
- `pygent-bugreport-instant-busy.md`：证明部分锁错误不会调用 busy handler，必须增加事务级重试；
- `pygent-bugreport-journal-quadratic-growth.md`：定位全量请求快照和无保留策略造成的 journal 写放大；
- 本报告：补充“所有 session 共享项目级 journal”这一部署边界，给出跨 session 历史证据和两层复现步骤。

## 结论

共享项目级 journal 不是唯一根因，但它把各 session 的持久化竞争、journal 体积和 checkpoint 成本耦合在一起，是本问题的重要放大器。当前 30 秒 `busy_timeout` 只能缓解普通写锁等待，不能处理失效 WAL 快照等立即失败。短期必须加入 rollback + bounded retry；中长期应同时治理 journal 写放大，并考虑按 session 分片 execution journal。
