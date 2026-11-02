# LARA：Layered Autonomous Reactive Agent 架构总纲

> 状态：目标定义阶段。本文只记录已对齐的目标、不变式与决议，作为后续设计与实现的基准；实现未开始。规范性结论以本文为准，本文未尽事项见 [6. 未决问题](#6-未决问题)。

## 1. 命名与定位

**LARA = Layered Autonomous Reactive Agent。**

- **表现形式是单 agent**：用户永远只面对一个 Lara。
- **实体是多 agent**：前台一个 agent，后台是数量不定、按需生长的 agent 群。
- **永续会话（eternal conversation）只是本架构中的一个应用**，不是全部。

## 2. 目标

### G1 单 agent 表现形式

用户视角下只有一个 Lara。后台 agent 的工作不以"另一个对话"出现，而以**结果**浮现：记忆被更新、快照被推进、reminder 被注入、异步消息被送达。

### G2 多 agent 形态：前台层 + 后台层

- **前台层**：反应式，响应用户与环境。
- **后台层**：自主式，数量不定。后台 agent 两类出身：
  - **fork agent**：上下文派生，继承身份与上下文，被 reminder 转向新任务；
  - **完全独立 agent**：拥有自己的上下文与目标。
  - 出身不构成特权差异；两类 agent 的工具面、权限边界一致。

### G3 fork 原语完全开放（"怎么 fork 都行"）

fork 是 LARA 的上下文派生原语，四个自由度全部开放：

| 自由度 | 开放范围 |
|---|---|
| 源 | 前台当前回合、checkpoint、session 区间、另一 fork 的产物、独立 agent 的上下文 |
| 切点/切片 | 消息流任意位置、任意窗口 |
| 变换 | 注入 0..n 条 `<system-reminder>`；如需可覆写系统提示 |
| 链式 | fork 的产物是一等上下文，可再被 fork、被引用、被延续 |

机制级复用：fork 是原语，extractor 只是第一个消费者；后续后台任务（builder、校验、总结类）都长成"fork + reminder + 标准工具"的形状。

### G4 工具面统一机制（架构保障，非绝对禁令）

工具统一是**机制层面的架构保障**，不是"任何 agent 永远不许有自定义工具"：

- **默认统一**：所有 agent——前台与任意后台——默认共用同一套标准工具面与同一条工具管线（定义 schema、鉴权、审计、副作用追踪、可观测性）。
- **自定义工具的合法通道**：不排除未来特殊 agent 拥有自定义工具；但自定义工具必须经由同一机制注册与接入——同一 schema、同一鉴权/审计/副作用/事件路径——架构上保证不存在绕过统一管线的旁路工具。
- **优先级**：任务特有能力优先以 skill / CLI 提供（现有 dynamic-memory-cli 即此角色），自定义工具是 skill/CLI 不适用时的补充。
- **报备约束**：新增工具 / skill / CLI（无论标准还是自定义）必须提前向用户报备。

### G5 权威边界不因 fork 改变

agent 只能通过具备校验与原子性的 CLI 提案；触发、冻结、覆盖 cursor 推进、原子发布等正式状态激活权属于 harness / runtime。fork 改变的是"agent 看到什么、用什么干活"，不是"它有什么权力"。

### G6 提取/构建类后台任务 = fork agent 的第一个实例

后台记忆任务的上下文结构（三块）：

```
├─ block 1  fork 的前台上下文（无损）
│    前台系统提示 + 原样消息流（用户消息 → assistant+tool_calls → tool results）
├─ block 2  任务转向（一条 user 消息）
│    <system-reminder>：冻结声明 + 提取/构建契约（沿用实验验证过的提示词正文）
│    + 状态指针（CLI 路径、--root、base revisions）
└─ block 3  自身多轮 ReAct
    与前台一致的工具集；查证、调 CLI、按校验结果修正、直到产出
```

与现行实现的本质差异：现行 extractor 接收**有损 JSON 摘要**（`_compact_working_memory`）+ 专属 memory 工具；目标态是无损 fork + 统一工具 + CLI 提案。

### G7 提案校验下沉到 memory-cli（决议 A，已批准）

现行契约校验在 lara 运行时工具处理器内（`_validate_extractor_payload`：快照 4/4/6/4/4/6 条限额、UT 限额、must_include 子串、`_normalize_changes`）。agent 经 bash 直连 CLI 后，该校验必须移植进 memory-cli 的 `publish-pending`（跨仓库改动，`E:\Projects\memory-cli`），使发布门禁对任意 agent 生效。**实现排后，方向已定。**

### G8 文档目录与代码模块命名空间并行

- 文档：本目录（`docs/design/lara-architecture/`）。
- 代码：`src/lara/agents/`（2026-11-02 落地基本形态）：
  - `context.py` — `ContextWindow`（一等可 fork 上下文）、`ContextSource`（`StaticContextSource` / `ForkProductSource`，协议可扩展）、`MessageSlice`、`ContextTransform`（`ReminderTransform` / `SystemPromptTransform`）、`messages_from_history`（复用前台会话转换器）；
  - `runner.py` — `AgentRunner`（唯一调用缝隙：每次运行独立组装 pygent ReAct）、`ToolSurface`（G4 统一工具缝隙）、`AgentLimits`、`build_model_layer`；
  - `fork.py` — `ForkSpec` + `ForkAgent`（block 1 由 source+slice+transforms 物化，block 2 = directive，block 3 = 多轮 ReAct；`plan()` 可在花费模型调用前检视前缀）；
  - `child.py` — `ChildSpec` + `ChildAgent`（独立出身，同缝隙）；
  - `main.py` — `MainAgent`（单 agent 表现形式：前台回合 + `spawn_fork`/`spawn_child` 后台任务 + `collect()` 把失败收集为结果而非抛出）。
- 尚未与持久化运行时融合，缺口清单见 [6. 未决问题](#6-未决问题)。

## 3. "分层"的维度（开放，逐步完善）

已确认两维：

1. **上下文分层**：Working Memory / Memory Snapshot / Long-term Memory / Raw History（永续会话四层模型，见 `docs/guides/eternal-conversation-overview.md`）。
2. **agent 分层**：前台层 / 后台层。

可能的第三维（未定，后续逐步完善）：自主性分层（如 L0 前台 / L1 fork / L2 独立 / 更高层编排）。

## 4. 现有机制 → 目标架构的归一映射

| 现有机制 | 在 LARA 中的位置 |
|---|---|
| 前台 LaraAgent 回合（durable execution） | 前台层 |
| EternalConversationHarness 的 extractor/builder worker | 后台层最早的调度器雏形；extractor 将改造为 fork agent 实例 |
| automation 会话（如巡检任务） | 独立 agent 的前身 |
| agent-collaboration 子会话 / delegation 配置 | 后台 agent 通信与调度的前身 |
| dynamic-memory-cli | skill/CLI 能力层的参照实现 |

## 5. 决议记录（2026-10-31 讨论对齐）

- **D1** extractor 改造为 fork 机制；三块上下文；block 3 为多轮工具调用。
- **D2** default/dev/fast-check 三 agent 无专属工具；UT 写入与构建操作经 bash 调用 dynamic-memory-cli；新增工具/skill/CLI 须提前报备。
- **D3** 提案契约校验移植进 memory-cli 的 publish-pending（方案 A，已批准；实现待后）。
- **D4** fork 机制要灵活：源/切片/变换/链式全部开放；机制可复用，extractor 只是第一个实例。
- **D5** "分层"维度后续逐步完善，本文档先记录已确认两维。
- **D6** 文档目录与代码模块命名空间都要；先定文档，把目标固化在文档里。
- **D7**（2026-11-02 补充）工具面统一是机制级保障而非绝对禁令：默认统一工具面与统一管线；特殊 agent 允许自定义工具，但必须经同一机制接入，架构上不存在旁路工具（对应 G4 修订）。
- **D8**（2026-11-02）脚手架实现启动：`src/lara/agents/` 落地主 agent / fork agent / 独立子 agent / 统一调用缝隙的基本形态；单元测试与 pyright 全绿；持久化集成与真实工具接线排后。
- **D9**（2026-11-02 架构评审后修订）评审结论：初版脚手架只是概念验证，未守住 G4/G5 声明的边界（工具面可绕过审批/审计、prefix 路径断裂、任务级 limits 被静默丢弃、取消传播破坏收集器、切片可撕裂交互帧、模型工具声明与工具面双重来源）。已修订：工具改为 `ToolProfile` 引用 composition root 注册的 `ToolRegistry`（agent 不得提交可执行 Module）；模型层改为按次解析的 factory，与工具面单一来源；切片强制交互帧对齐（`align_slice_bounds`）；fork 记录 `ForkSnapshot`（source/游标区间/内容 hash）；`BackgroundJobHandle` 替代裸 Task；`collect()` 把取消与失败都收敛为结果。**仍未解决（属运行时职责）**：持久化 job 记录、capacity lease、deadline、EventStore 事件、生产级 ForegroundToolSurface——见 §6.8。
- **D10**（2026-11-02 第二轮评审后修订）修复：`collect()` 用 `asyncio.shield` 隔离 job——取消 collect 只停止等待，job 继续运行、句柄归还原注册表、部分结果缓冲到下次收集，取消本身向上传播；`ReminderTransform` 插入点强制吸附到交互帧边界，不再撕裂 tool call/结果；`align_slice_bounds` 修复合法空切片的越界（返回空而非 IndexError），并对连续多条 ToolMessage 一并补齐帧；`ForkSnapshot` 区分 `source_hash`（变换前）与 `effective_context_hash`（变换后）并记录结构化 transform 描述（`ContextTransform.describe()`）；job_id 改为 UUID、label 仅作展示。**明确接受的已知阻塞**：`ToolSurface` 协议在接入生产前台管线前需扩展 context factory 与 post-tool 审计钩子（生产管线的审计/EventStore/file effects 发生在工具执行之后且依赖 managed `LaraContext`）——见 §6.8。
- **D11**（2026-11-02 第三轮评审后修订）修复 collect 所有权与注册表结构：注册表改以唯一 job_id 为键，label 移入独立的 in-flight 集合且**只在 outcome 交付后释放**（collect 取消期间不再产生重复 label 竞态或句柄覆盖）；`collect()` 全程持有 `asyncio.Lock` 串行化并发调用（不再有 KeyError 竞态）；等待阶段不再提前弹出 handle，取消时无需恢复、无 job 丢失。快照描述符补全内容（reminder 存包装后文本、system_prompt 存新提示词），使 snapshot+source 可重建 agent 实际所见。**遗留**：按 `tool_call_id` 校验帧完整性、session/cursor/revision 结构化来源、生产 `ToolSurface` 协议扩展（见 §6.8）。
- **D12**（2026-11-02 第四轮评审后修订）两处收紧：① 快照描述符改为**内容寻址**——transform 载荷（reminder 文本、system prompt）只存 SHA-256 hash，不再把全文写入 context metadata，避免敏感指令/状态指针随 durable context、EventStore 或诊断导出扩散；内容本身仍由运行结果的 context 承载（那才是持久化工件），hash 保持可校验性。② spawn 的 label 预留在 task 创建失败（如无运行事件循环）时回滚，并 close 未启动的协程——label 不再泄漏。

## 6. 未决问题（实现前需回答）

1. **fork 原语签名**：源引用格式、切片表达、reminder 注入点、产物生命周期与归属会话。
2. **fork 上下文的持久化与缓存归属**：block 1 与前台请求前缀一致时的 prompt cache 复用策略。
3. **后台 agent 生命周期**：并发上限、步数预算、空闲超时、失败重试与**显性告警**（教训：本会话 extractor 曾 861 次静默失败无人感知；启动时必须校验 agent 别名与配置，失败必须可见，必须有降级路径）。
4. **后台 agent 对 workspace 的写权限纪律**：建议 reminder 声明只读纪律（待定）。
5. **存量会话 bootstrap**：提取分批上限（建议沿用 500 轮实验验证过的 200 条/批）。
6. **老 agent 定义恢复**：default/dev/fast-check 三 agent 落回主配置（9-15 备份 `config.pre-model-connections-20260915-190310.yaml` 可参照：dev=提取器、fast-check=构建器、能力经 CLI）。
7. **统一工具机制的形态**：自定义工具的注册 schema、鉴权/审计/副作用接入点、可携带自定义工具的 agent 范围与审批流程（落实 G4 的架构保障）。
8. **脚手架与持久化运行时的融合**（2026-11-02 起）：前台 MainAgent 正式形态接入 durable execution（现有 runtime/service.py 路径）；**`ToolSurface` 协议扩展**——生产前台管线的审计、EventStore、file effects 发生在工具执行之后并依赖 managed `LaraContext`，接入需要 context factory、post-tool 审计钩子与 managed runtime binding，当前两成员协议（definitions + tool_module）不足以承载（D10 记录的已知阻塞）；RunConfig→模型层工厂与 alias→child 配置解析（含恢复 dev/fast-check 定义）；run 事件接入 EventStore；后台 job 并发上限/超时/显性告警与 capacity 对接、持久化 job 记录；`SessionRangeSource`（session 区间直接作 fork 源，含 session/cursor/revision 结构化标识）等扩展源。

## 7. 未来展望

### 7.1 实时问题回答 fork agent（规划中，未设计）

后续会有一个 fork agent 专职做实时问题回答：从前台上下文 fork（block 1），以一条 reminder 转向为问答任务（block 2），用与前台一致的工具多轮工作并即时作答（block 3）。

- 预期场景：前台执行长任务期间，用户的插问由该 fork agent 基于**与前台一致的上下文**实时回应——不打断前台工作、不在前台上下文里留下问答噪声。
- 与单 agent 表现形式的关系：回答以结果形式送达用户，用户感知仍是"同一个 Lara 在回应"（G1）。
- 架构意义：它是 fork 原语"同一主干 + 不同 reminder = 不同后台任务"（G3）的第二个消费者，用于验证 fork 机制的通用性。

## 8. 相关材料

- 永续会话设计：`docs/design/agent-self-optimization/`、`docs/guides/eternal-conversation-overview.md`、`docs/principles/eternal-conversation-*.md`
- 实验证据：500 轮验收（`eternal-blind-500`，隔离 HOME 含 default/dev/fast-check 三 agent，连续通过 300 轮）；主配置 extractor 861 次同因失败记录；卡住会话诊断（全量 1940 条历史 ≈ 86.6 万 input tokens 撞 60s 空闲超时）。
