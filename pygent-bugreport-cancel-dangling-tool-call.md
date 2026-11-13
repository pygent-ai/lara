# Bug Report: 执行级取消(asyncio cancel)下 ToolCallLayer 不产出配对工具结果 —— 中断的工具调用在 transcript 悬空,后续每轮恢复投影都携带不配对的 tool_use,严格校验供应商 400 后会话永久卡死

> **状态:未修复(0.3.31 现行)。** 复现于 pygent-ai 0.3.30(PyPI wheel,宿主集成实测);0.3.31 源码逐行核对,相关路径(`_execute_sync` 的取消直通、`_interrupted_call_result` 仅挂 steering 中断事件)无差异。悬空 → 供应商 400 的最后一跳是公开 API 契约行为(Anthropic/OpenAI 均校验 tool_use/tool_result 配对),未做真实网络重放;其余环节全部有代码级与运行时证据。

## 环境

| 项 | 值 |
|---|---|
| pygent-ai | 0.3.30(复现实测)/ 0.3.31(源码核对) |
| Python | 3.13,Windows |
| 装配形态 | Lara agent:`ToolCallLayer` 标准装配 + `DurableToolTaskManager` 已挂载(`runtime.attach_tool_task_manager`)+ 持久化绑定 ExecutionOptions;`ToolAuditModule`/checkpoint 模块包裹工具层 |
| 发现途径 | Lara 侧端到端取消测试:真实托管执行 + 脚本化模型 + 真实 bash 工具,工具执行中途 `handle.cancel()` |

## 摘要

`ToolCallLayer` 对"工具批次被打断"只有一条收尾路径:**steering 中断事件**(`step_interrupt_event()` + `gather_until(tracked, abort=event)`,layer.py:161-167、200-222)——未完成的调用逐个走 `_interrupted_call_result`(layer.py:329-372),从任务管理器按 `call_id` 重建 `status="detached"` 的 ToolResult(带任务引用与查询方式),工具后台继续跑,transcript 保持配对。

**执行级取消**(用户停止轮次 / deadline 超时 → `asyncio.CancelledError` 终结整个 execution)没有对应分支:`_execute_sync` 的执行体 `except asyncio.CancelledError: raise`(layer.py:505),异常穿透 gather 与 `forward`,`forward` 不产出任何 ToolMessage。宿主的对话 checkpoint 按"稳定边界"落盘,assistant 的 tool_calls 已落盘而工具结果永远缺失——**transcript 尾部悬空**。

后果分两级:

1. **下一轮恢复投影携带悬空调用**(`[..., user, assistant(tool_use), user(新消息)]`);Anthropic 原生与 OpenAI 原生 API 均校验配对并返回 400 `invalid_request`。
2. **会话永久卡死**:悬空 assistant 永久留在 transcript 与宿主 checkpoint 尾巴里,后续每一轮恢复都重放它 → 每轮都在同一处 400(`INVALID_REQUEST` 不可重试、不触发 fallback)→ 用户无论发什么消息都报同样的错。唯一出路是 fork 到悬空点之前或手工修会话数据。

## 中断模式对照(缺口定位)

| 中断模式 | 触发 | 收尾行为 | 悬空 |
|---|---|---|---|
| steering WAIT | steering 消息在下一处理边界生效 | 步骤正常跑完 | 否 |
| steering IMMEDIATE | steering 打断 in-flight 步骤 | `interrupt_event` → `gather_until` → 未完成调用 `_interrupted_call_result` → detached 结果带任务引用,轮次继续 | 否 |
| **执行级取消** | `handle.cancel()` / deadline 超时 → asyncio cancel | **CancelledError 裸穿**,`forward` 无 ToolMessage 产出 | **是(本缺陷)** |
| 进程崩溃 + 恢复 | host 死亡后 recover | effect journal replay(REPLAY_SAFE)重放/补完工具;`recover_tool_jobs` | 否 |
| 进程崩溃 + 不恢复直接开新轮 | 同上但放弃恢复 | 悬空 transcript 进入恢复投影(同一缺口的二阶暴露) | 是 |

缺口不在能力缺失:任务管理器的 `get_task_by_call` 文档原话就是"lets the tool layer attribute an interrupted batch call to its admitted task **even though the admitting coroutine was cancelled**"(executors.py:953-959)——基础设施本为"admit 后被取消"设计,rebuild 在执行级取消场景同样成立(持久化任务本就比 execution 活得久)。缺的只是 `forward` 上的取消分支。

## 最小复现(宿主集成实测)

脚本化模型:第 1 轮正常应答;第 2 轮返回 `bash {"command": "sleep 8"}`,待 assistant 工具调用 checkpoint 落盘后立即 `handle.cancel()`;第 3 轮发新消息并检查模型实际收到的投影。

实测结果(0.3.30 wheel):

| 断言点 | 结果 |
|---|---|
| 取消轮终态 | `status="skipped"`;transcript = `[user, assistant, user, assistant(tool_calls)]`,**无 tool 消息** |
| checkpoint | 停留在第 1 轮(2 条消息) |
| 第 3 轮模型投影 | `[user, assistant, user, assistant(tool_calls)]` —— 悬空调用进入 wire 请求 |
| 接严格供应商时 | 该请求 400 `invalid_request`(tool_use 无紧跟 tool_result);此后每轮同点失败 |

复现测试(可直接作为上游回归的宿主侧对照):`tests/unit/test_runtime_recovery.py::test_next_turn_replays_the_conversation_of_a_cancelled_mid_round_turn`(Lara 仓)。宿主侧因素已排除:`DurableToolTaskManager` 已挂载、checkpoint 链路按边界幂等落盘、恢复投影逐字重放悬空条目。

## 根因(0.3.31 源码定位)

1. **取消直通**:`_execute_sync` 执行体 `except asyncio.CancelledError: raise`(layer.py:505;:273、:568 同模式),异常穿透 gather → `forward` → 调用链上层。
2. **rebuild 仅挂 steering 事件**:`forward` 只有 `interrupt_event` 非空时走 `gather_until` + `_interrupted_call_result`(layer.py:200-222);执行级取消没有 `except CancelledError` 分支去复用它。
3. **兜底本就存在**:`_interrupted_call_result`(layer.py:329-372)按 `call_id` 查任务管理器重建 detached 结果;查不到任务时 `_cancelled_result`(layer.py:314)返回 `status` 为取消的 ToolResult——**两条路径都产出配对消息**,最坏也是"已取消"而非"无回应"。

## 期望行为

执行级取消时,未完成的工具调用与 steering 中断同等收尾:任务管理器有任务 → `status="detached"` 结果(带任务引用,模型下轮可查询任务进度);无任务 → 取消结果。组装 ToolMessage 后继续传播取消。宿主 checkpoint 由此落盘配对边界,transcript 不再悬空。

## 修复建议

1. **根治**:`ToolCallLayer.forward` 捕获执行级取消——对每个未完成调用执行 `_interrupted_call_result`/`_cancelled_result`,组装 ToolMessage 后向上重抛 CancelledError(或交由宿主 checkpoint 后终止)。steering 路径行为不变。
2. **回归测试**:复现"工具执行中取消 → transcript 以配对工具结果收尾 → 下一轮投影无不配对 tool_use";补充"任务管理器无任务 → cancelled 结果"的降级断言。
3. **宿主受益面**:Lara 的对话 checkpoint、eternal 投影、恢复路径均按 ToolMessage 落盘,上游修复后宿主零改动;eternal 会话(恢复尾巴按 history 重放)同样消除该悬空。

## 补充上下文

- 宿主侧对照分析(排除接线问题):steering 重建、`DurableToolTaskManager`、`recover_tool_jobs` 在宿主均已接线;崩溃+恢复路径因 effect replay 不受影响。
- 影响评估:所有带工具且在工具执行中被取消的轮次;工具执行时间越长命中率越高。宽容网关(自动截断/容忍不配对)可能不立即 400,但悬空调用仍会误导模型上下文。
- 复现与断言脚本见 Lara 仓测试;悬空条目在 provider 为脚本模型时轮次"成功",便于隔离上游修复前后的行为差异。
