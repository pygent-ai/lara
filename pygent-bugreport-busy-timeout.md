# Bug Report: SQLiteHistoryStore 未设置 busy_timeout,并发写者下默认 5 秒超时导致 `OperationalError: database is locked`

## 环境

| 项 | 值 |
|---|---|
| pygent-ai | 0.3.19(`pygent-ai[video]`) |
| aiosqlite | 0.22.1 |
| Python | 3.13.3 |
| SQLite | 3.49.1 |
| 平台 | Windows(journal 位于本地盘 NTFS) |
| 部署形态 | 多个 LocalRuntime 进程/会话共享同一个项目级 journal DB(WAL 模式),journal 约 781 MiB |

## 摘要

`SQLiteHistoryStore.open()` 以 `aiosqlite.connect(self.path)` 打开连接,没有设置 `timeout`,也没有执行 `PRAGMA busy_timeout`(`pygent/runtime/_history_store.py:108-110`)。因此 journal 上的所有写事务都继承 sqlite3 默认的 **5 秒** busy timeout。在多 runtime / 多进程共享同一 journal 的部署下,一旦某个连接的写事务(例如事件批量 flush)持有 WAL 写锁超过 5 秒,另一连接的任何写操作都会抛出 `sqlite3.OperationalError: database is locked`,且 `SQLiteHistoryStore` 的构造参数中没有暴露任何可配置项,调用方无法缓解。

## 生产现场(Lara 集成,偶现错误的一次实录)

Lara 使用 pygent LocalRuntime 的 durable tool task。一次事故时间线:

1. `03:26:29Z` 发起 detached bash 任务 `tool-9c9ad0e8-9b7e-4309-bdcd-1dc26de1ac50`;
2. `03:44:36Z` 所属 turn 因 deadline 到期结束;
3. `03:50:36.435Z` 后台终结观察器在 journal 写路径上抛错,记录为 `runtime.error`:

   ```json
   {"error": "database is locked", "error_type": "OperationalError",
    "phase": "bash-finalization", "task_id": "tool-9c9ad0e8-9b7e-4309-bdcd-1dc26de1ac50"}
   ```

4. 同一时刻,同项目的另一个会话 run 正处于活跃状态并持续向同一 journal 写执行事件(其 `context.checkpoint` 事件在错误后 200ms 落盘);
5. 观察记录按设计持久化,直到 `03:50 +95min` 后的下次 runtime 启动才由重试路径完成终结——一次瞬时锁竞争被放大成约 95 分钟的审计延迟和一条用户可见的错误事件。

触发路径:`DurableToolTaskManager.get_result`(`pygent/runtime/tasks.py:567`)→ `_recover_observation`(`tasks.py:471`)→ `finalize_lost_tool_owner`(`pygent/runtime/_history_jobs.py:97`,显式 `BEGIN IMMEDIATE`)。事件批量 flush、`put_tool_output` 等其它 journal 写路径同样暴露在该问题上。

## 根因

- `pygent/runtime/_history_store.py:108`:`self._connection = await aiosqlite.connect(self.path)` —— 无 `timeout` 参数;
- `pygent/runtime/_history_store.py:109-110`:仅设置 `journal_mode=WAL` 与 `foreign_keys=ON`,无 `busy_timeout`;
- `SQLiteHistoryStore.__init__`(`_history_store.py:62`)只暴露 `max_event_batch_size` / `max_pending_event_batches` / `max_transaction_batch_size` / `max_pending_transactions`,**没有任何锁等待相关配置**,调用方无法注入;
- 对照组:capacity coordinator 明确做了 `sqlite3.connect(..., timeout=5.0)` + `PRAGMA busy_timeout = 5000`(`pygent/runtime/capacity.py:474-485`)。history store 作为更高频的写入口反而依赖隐式默认值。

放大因素(推断,未做 profiling):781 MiB 的 WAL 库 + 默认 `synchronous=FULL` 的每次提交 fsync,在 Windows 上叠加杀毒实时扫描,单个批量写事务持锁时间尾部会超过 5 秒。

## 最小复现

普通连接模拟"另一个 runtime 的慢写者"持锁 8 秒;pygent store 以其默认连接配置执行一次 journal 写(`admit_tool_observation`):

```python
import asyncio, sqlite3, threading, time
from pathlib import Path
from pygent.runtime import SQLiteHistoryStore

DB = Path("repro-journal.sqlite3")
t0 = time.monotonic()

def holder():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("BEGIN IMMEDIATE")          # 持有 WAL 写锁 8 秒
    print(f"[holder ] lock acquired at +{time.monotonic()-t0:.1f}s, holding 8s")
    time.sleep(8)
    conn.execute("COMMIT"); conn.close()
    print(f"[holder ] committed at +{time.monotonic()-t0:.1f}s")

async def main():
    if DB.exists(): DB.unlink()
    store = await SQLiteHistoryStore(DB).open()   # 建表完成后 holder 才开始持锁
    thread = threading.Thread(target=holder)
    thread.start()
    await asyncio.sleep(0.5)                 # holder 已持锁
    try:
        await store.admit_tool_observation("task-1", "owner-1", 60.0)
        print("[victim ] write unexpectedly succeeded")
    except Exception as exc:
        print(f"[victim ] {type(exc).__name__}: {exc} after {time.monotonic()-t0:.1f}s")
    thread.join()                            # 等 holder 提交
    await store.admit_tool_observation("task-1", "owner-1", 60.0)
    print("[victim ] retry after holder commit: ok (lock contention only, no corruption)")
    await store.close()

asyncio.run(main())
```

实测输出(pygent-ai 0.3.19 / Python 3.13.3 / Windows):

```text
[holder ] write lock acquired at +0.0s, holding 8s
[victim ] OperationalError: database is locked after 6.1s
[holder ] committed at +8.0s
[victim ] retry after holder commit: ok (lock contention only, no corruption)
```

(6.1s ≈ 0.5s 偏移 + 默认 5s busy timeout + 连接调度开销;锁释放后同一写操作立即成功,证明是纯锁等待超时,无数据损坏。)

## 期望行为

- journal 连接的锁等待时间可配置,且默认值足以覆盖同机多 runtime 的正常写竞争(不应依赖 sqlite3 的 5 秒隐式默认);
- 短暂的写锁竞争不应以 `OperationalError` 的形式穿透到上层调用方(如 `get_result` / `finalize_lost_tool_owner`),至少在文档中明确该失败模式与调用方需要的重试义务。

## 修复建议

1. **最小修复**:`open()` 中 `aiosqlite.connect(self.path, timeout=<configurable>)`(或追加 `PRAGMA busy_timeout = <ms>`),并把该值作为 `SQLiteHistoryStore.__init__` 的关键字参数暴露;默认值建议 ≥ 30s。
2. **可选增强**:对写路径(`BEGIN IMMEDIATE` / 事件批量 flush)做针对 `SQLITE_BUSY` 的有限次退避重试。
3. 可一并评估 WAL 下 `synchronous=NORMAL` 的取舍(属持久性语义变更,仅作为讨论项)。

## 补充上下文

- 数据库本身健康:错误后重试全部成功,观察记录最终一致。
- 单 journal 文件随使用增长(本项目一天达到 ~781 MiB),会系统性拉长持锁时间,建议在文档中给出容量/归档方面的运行建议。
