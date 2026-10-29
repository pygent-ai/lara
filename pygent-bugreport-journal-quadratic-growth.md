# Bug Report: journal 随会话长度 O(n²) 膨胀 —— 每次模型调用全量重写请求快照,流式增量与大快照终态后永不修剪(5.4 GiB 实录)

## 环境

| 项 | 值 |
|---|---|
| pygent-ai | 0.3.23(editable 源码 `E:\Projects\pygent`;`pip show` 版本号报 0.2.10,与 lora `pyproject.toml` 声明不一致,行为以源码为准) |
| aiosqlite | 0.22.1 |
| Python | 3.13.3 |
| SQLite | 3.49.1(WAL 模式) |
| 平台 | Windows(journal 位于本地盘 NTFS) |
| 部署形态 | Lara 桌面端,多会话共享同一项目级 journal:`…\.lara\projects\<project>\.lora\runtime\executions-v1.sqlite3` |
| journal 体量 | 5,712,269,312 字节(5.32 GiB);page_count 1,394,597 × 4096;**freelist 仅 259 页(≈0.1 MB)** —— 不是碎片/已删数据膨胀,是真实存了这么多数据 |

## 摘要

三个独立缺陷叠加,使 journal 按会话长度 O(n²) 增长:

1. **每次模型调用(含每个重试 attempt)都把完整请求投影作为 `model.request.prepared` 事件整体写入 events**:system_prompt + 全部历史 messages + current_message + 全部 tools 定义(schema 全文)+ generation + media_projections。同一执行内第 k 次调用的载荷随 k 线性增长,总量 O(n²)。
2. **journal 无任何终态保留策略**:118 万条流式 delta 事件(`model.reasoning.delta` / `model.text.delta`,单条均值 ~0.6 KB)在执行终结后永不清理;大快照事件同样永不修剪。
3. **大载荷明文 JSON 行内存储**:无压缩、无内容寻址去重、无 out-of-line。快照字段自证 `compression_version: 0`。

## 生产现场(2026-10-10 实测)

project `916f2c545f1d3c118b591db9`,覆盖 2026-09-20 ~ 09-24 期间共 **135 个执行** 的 journal:

表级载荷(SUM(LENGTH(各 JSON 列))):

| 表 | 行数 | 载荷 | 说明 |
|---|---|---|---|
| events | 1,179,077 | **3.48 GB** | 见下方分桶 |
| effects | 10,822 | **1.73 GB** | 其中 `lora.context.snapshot` 1,118 行 = **1.62 GB**,所属执行 updated_at 最大为 2026-09-21 02:51:44 UTC;当前 pygent/lora 源码已无该写入点(已核对全部 `execute_effect` 调用点),属旧版运行时遗留死数据 |
| executions | 135 | 0.36 GB | 单行 input_json + output_json 各 ~2.6–3.0 MB,同样内嵌全量上下文 |
| 其余(tasks/jobs/tool_task_observations 等) | — | <10 MB | 正常 |

events 按单条尺寸分桶:

| 桶 | 行数 | 合计 | 内容 |
|---|---|---|---|
| > 1 MB | 961 | **1.66 GB** | 抽查均为 `model.request.prepared` 全量快照(实测最大单条 2.95 MB:`messages` 417 条、system_prompt 14,951 字符、tools 13 个) |
| 0.1–1 MB | 1,842 | 0.92 GB | 同类快照的中等规模实例及个别大工具输出 |
| < 0.1 MB | 1,176,274 | 0.71 GB | 抽样 4,000 条(取最新)全部为 `model.*.delta` / `span.*` / `tool.*` / `effect.executed`,均值 0.6 KB |

关键对照:同一 journal 里真正执行模型调用的 `model.invoke` effects(1,688 行)合计只有 **10.8 MB** —— 模型调用的固有持久化记录很小,**膨胀几乎全部来自观测性快照的反复重写与不修剪**。

## 根因

1. **per-attempt 全量快照**(`llm/invoker.py:565`):`prepared_event = prepared_request_event(request, attempt=number)` 位于逐 attempt 循环内,每个 attempt 都 `emit(ModelEventKind.REQUEST_PREPARED, prepared_event)`;`llm/_request_snapshot.py:_request_projection` 将 `request.context.system_prompt`、**全部** `request.context.messages`、`current_message`、全部 tools 的 name/description/parameters/output_schema、generation、media_projections 整体序列化。相邻两次调用的消息历史互为超集(差量仅在尾部若干条),却每次整份重写 → Σ ≈ n²/2 × 单次增量。重试 attempt 场景下 `request_digest` 完全相同,2.9 MB 载荷原样再写一遍。
2. **无终态保留**:`runtime/_local/state.py` 的 finalize 与 history store 均不清理 events;delta 流最终会汇入 assistant 消息,执行终态后即纯冗余,却永久留在 events 表。
3. **无压缩、无去重、无 out-of-line**:`_request_snapshot.py` 已计算 `request_digest`(sha256 规范化 JSON),但仅作为字段记录,未用于载荷去重;journal 旁已有边车目录先例(lara 侧 `.bash-observations`),大载荷却仍走行内 JSON。
4. **执行记录放大**(旁证):135 个执行行的 input/output JSON 内嵌等量上下文,合计 0.36 GB。

## 量化/复现

在任一长期使用的 journal 上执行:

```sql
-- 大快照总量
SELECT COUNT(*), SUM(LENGTH(event_json))/1048576.0
FROM events WHERE LENGTH(event_json) > 1000000;

-- 按 kind 的行数与均长
SELECT json_extract(event_json,'$.kind') AS kind, COUNT(*), AVG(LENGTH(event_json))
FROM events GROUP BY 1 ORDER BY 2 DESC;

-- 确认非碎片膨胀
PRAGMA freelist_count;   -- 本现场:259 页
```

## 期望行为

- **快照存储随会话长度线性**:执行内首个请求存全量,后续仅存相对前一快照的差量(新增 messages + 被裁剪数 + 基引用 + projection_revision);上下文压缩导致 projection_revision 变化时重置链、重拍一次全量。
- **大载荷 out-of-line + 压缩**:内容寻址(zstd 或 zlib),events/effects 行内仅存 `{digest, size, codec}` 引用;重试 attempt 的相同 `request_digest` 载荷零边际成本。
- **终态有界保留**:执行终结后清理/折叠 delta 类事件;快照保留末尾 K 次请求(消费方 lara 的 Trace 面板本就按 `list_window(limit)` 窗口读取,lara `model_request_journal.py:26` 以 `kind='model.request.prepared'` 过滤 —— 格式变更需读写两侧协同,但两侧同仓可改,无外部兼容约束)。
- 上述修复不影响恢复语义:effect 重放依赖 effects 表而非 events,删除观测性事件不触及 durable execution 正确性。

## 修复建议

1. **增量链快照**(pygent 写侧):`prepared_request_event` 拆分为首拍全量 + 后续 delta;delta 携带 `base_snapshot_ref/digest` 与新增消息段。
2. **out-of-line blob store**(pygent journal 侧):新增 `executions-v1.request-blobs/` 内容寻址目录(仿既有 `.bash-observations` 模式),zstd 压缩,文件名 = sha256。
3. **finalize 保留策略**(pygent):终态执行删除 delta 类事件,快照仅保留最后 K 个;K 可配置。
4. **存量清理**(lara 侧配合,破坏性操作):删除遗留 `lora.context.snapshot` effects(1.62 GB)、终态执行的历史 delta(0.7 GB)、旧格式大快照事件(≈2.5 GB),随后 `VACUUM`;本现场预计回收 **≈4.5 GiB**,journal 预期回落到 100–300 MB 量级。

## 与 pygent-bugreport-instant-busy.md 的关系

该报告修复建议 #4 已把"journal 容量增长(当时 2.0 GiB)拉长持锁尾部"列为讨论项。本报告是该讨论项的实证与定位:容量增长的主因不是锁竞争或写放大,而是观测性快照的 O(n²) 重写与零修剪;容量治理若只做 checkpoint/同步参数调整无法治本,需先解决本报告的写侧问题。
