# Bug Report: 立即返回类 SQLite 锁失败不受 busy_timeout 保护,且 SQLiteModelDeploymentStore 连接未设 timeout —— 单次瞬时锁错误判死整个 turn

## 环境

| 项 | 值 |
|---|---|
| pygent-ai | 0.3.23(PyPI,`pygent-ai[video]`) |
| aiosqlite | 0.22.1 |
| Python | 3.13.3 |
| SQLite | 3.49.1 |
| 平台 | Windows(journal 位于本地盘 NTFS) |
| 部署形态 | 多会话共享同一项目级 journal(WAL 模式),journal 约 2.0 GiB,WAL 约 4 MiB(恰在默认 autocheckpoint 阈值) |

## 摘要

`pygent-bugreport-busy-timeout.md` 报告的 busy_timeout 修复已在 0.3.23 落地(`SQLiteHistoryStore` 默认 `timeout=30.0` + `PRAGMA busy_timeout=30000`)。但本次事故证明:

1. **同包的 `SQLiteModelDeploymentStore` 被漏掉了**:连接仍是无 timeout 的裸 `aiosqlite.connect`(隐式 5 秒默认),且构造函数不暴露任何锁等待配置;
2. **busy_timeout 不是完整防线**:SQLite 存在"立即返回、不调用 busy handler"的锁失败类别(WAL 读事务升级写时的 `SQLITE_BUSY_SNAPSHOT`,以及同连接语句冲突的 `SQLITE_LOCKED`)。pygent/runtime 全部模块对 `OperationalError`/`SQLITE_BUSY` **没有任何捕获、重试或上下文包装**,一次瞬时锁错误即穿透为整个 turn 的致命失败。

## 生产现场(Lara 集成,2026-10-10 实录)

Lara 会话 `chat-chat-20261008-162349-aa0d36`,run `run-20261010-103305-263894-c2f5a5`,journal `…\.lara\projects\<project>\.lora\runtime\executions-v1.sqlite3`(2,067,259,392 字节)。事件时间线(UTC,取自 run 的 `events.jsonl`):

| 时刻 | 事件 |
|---|---|
| 02:33:06.207 | 用户消息入库,model.request 发出 |
| 02:33:44.861 | 模型回复完成,assistant message 持久化(含工具调用) |
| 02:33:45.052 | `runtime.file_scan.incomplete` |
| **02:33:45.079** | **`runtime.error`:`{"error": "database is locked", "error_type": "OperationalError"}`** |
| 02:33:46.559 | `model.response status=error` |
| 02:33:46.561 | `context.checkpoint status=error` → run 终态 `error`,计划中的工具调用未执行 |

关键签名:**错误距上一步仅 27 ms(距 assistant message 持久化 218 ms)**。而两条候选连接的锁等待下限都远大于此:

- `SQLiteHistoryStore`(journal):`busy_timeout=30000`(`_history_store.py:113,119,126`);
- `SQLiteModelDeploymentStore`(model-deployments-v2-*.sqlite3):无显式 timeout,隐式默认 5 秒。

同一时刻,同项目另一会话的 run(`chat-chat-20261010-102050-182fd1`,02:29:24–02:36:59,passed)正在向同一 journal 持续写入。**如果失败走的是普通写-写竞争,busy handler 应等待数秒而非 27 ms 内返回**——说明本次命中的是 handler 不介入的立即失败类别。错误为裸字符串 `"database is locked"`,无 DB 路径、无表名、无语句上下文,无法事后定位具体是哪个 store 的哪条语句。

## 根因

1. **`SQLiteModelDeploymentStore` 未设 timeout**(`model_deployment.py`):
   - `:395` `__init__(self, path, *, namespace_id=None)` —— 无锁等待参数;
   - `:411` `self._db = await aiosqlite.connect(self.path)` —— 无 `timeout`,无 `PRAGMA busy_timeout`。
   对照组:capacity coordinator 显式 `timeout=5.0` + `PRAGMA busy_timeout=5000`(`capacity.py:477-479`),history store 0.3.23 已达 30 秒。deployment store 是三个 store 中唯一裸连的。
2. **写路径对 handler 跳过类锁错误零防御**:全 `pygent/runtime` 无任何 `except OperationalError`/`SQLITE_BUSY` 处理(已 grep 确认);所有显式事务均为 `BEGIN IMMEDIATE`(`_history_store.py:467,569`、`_history_jobs.py:95,128,166,277`、`model_deployment.py:525,565,630,689`),但 `BEGIN IMMEDIATE` 只保护"获锁等待",不保护升级/同连接冲突类立即失败。单次失败直接作为 step 异常冒泡,终结整个 turn。
3. **错误无上下文**:`OperationalError` 原样上抛,调用方(Lara)的 trace 只能记录 `"database is locked"`,无法区分 journal / deployment store,也无法区分等待超时与立即失败两类签名。

## 最小复现(已实测验证)

WAL 模式下,连接 A 开 deferred 读事务(建立快照)→ 连接 B 提交一次写 → A 在同一事务内升级写:

```python
import sqlite3, tempfile, os, time
p = os.path.join(tempfile.mkdtemp(), "repro-upgrade.sqlite3")
a = sqlite3.connect(p, timeout=30.0, isolation_level=None)  # busy_timeout=30s
a.execute("PRAGMA journal_mode=WAL")
a.execute("CREATE TABLE t(x INTEGER)")
b = sqlite3.connect(p, timeout=30.0, isolation_level=None)
b.execute("PRAGMA journal_mode=WAL")

a.execute("BEGIN")                          # deferred 读事务,建立快照
a.execute("SELECT count(*) FROM t").fetchall()
b.execute("INSERT INTO t VALUES (1)")       # B 在 A 的快照之上提交
b.commit()
t0 = time.monotonic()
try:
    a.execute("INSERT INTO t VALUES (2)")   # A 升级为写:快照已过期
    print("A: upgrade unexpectedly succeeded")
except sqlite3.OperationalError as e:
    print(f"A: {type(e).__name__}: {e}  (after {time.monotonic()-t0:.3f}s, busy_timeout=30s)")
a.execute("ROLLBACK")
```

实测输出(pygent 环境同版本:Python 3.13.3 / SQLite 3.49.1 / Windows):

```text
A: OperationalError: database is locked  (after 0.000s, busy_timeout=30s)
```

0.000 秒即失败——`timeout=30.0` 完全未生效(SQLite 文档:`SQLITE_BUSY_SNAPSHOT` 类错误不调用 busy handler)。这与生产现场 27 ms 的失败签名一致,证明**单纯调大 busy_timeout 无法覆盖此类失败**。

## 期望行为

- 三个 store(connection)的锁等待行为一致、可配置;不应有裸 `aiosqlite.connect` 残留;
- 立即返回类锁错误(`SQLITE_BUSY_SNAPSHOT`、同连接 `SQLITE_LOCKED`、锁等待超时)应被 store 层以有界退避重试吸收,或至少文档中明确调用方必须重试的义务;
- 锁类错误上抛时携带 DB 路径与语句/表上下文,使生产 trace 可以定位。

## 修复建议

1. **最小修复**:`model_deployment.py:411` 改为 `aiosqlite.connect(self.path, timeout=<configurable>)` + `PRAGMA busy_timeout`,并在 `__init__` 暴露参数,默认与 history store 对齐(≥30 秒)。
2. **锁失败韧性**:store 写路径对锁类 `OperationalError` 做有界退避重试(对 `BEGIN IMMEDIATE` 前的读依赖,改用单事务 `BEGIN IMMEDIATE` 包住读+写,或失败后 rollback 重开事务重试),避免一次瞬时竞争终结整个 turn。
3. **可观测性**:捕获锁类错误时包装/链接异常,附 `self.path` 与失败语句摘要。
4. **讨论项**:journal 容量增长(本现场 2.0 GiB)会拉长持锁尾部;建议文档给出 WAL checkpoint/容量治理建议,并评估 `synchronous=NORMAL` 的取舍。

## 与 pygent-bugreport-busy-timeout.md 的关系

前一份报告的结论(busy_timeout 缺失 → 5 秒隐式默认)已在 0.3.23 的 history store 落地。本报告是其延续:同一部署形态下再次发生锁类致命错误,且( a )修复未覆盖兄弟类 `SQLiteModelDeploymentStore`;( b )实证 busy_timeout 对"立即返回"类别无效,需要重试纪律与错误上下文,而非更大的超时数值。

## 补充上下文

- 数据无损:事故 run 的助手回复已持久化,工具调用未执行;并发 run 与后续重试均正常通过,证明为纯瞬态锁竞争。
- 事故的具体失败语句未经插桩无法指认(两条候选连接、两类立即失败机制均与签名吻合),这也是"错误无上下文"问题本身的代价。
