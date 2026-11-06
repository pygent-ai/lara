# 实时交互（Real-time Interaction）设计

> 状态：设计定稿阶段。本文记录已对齐的背景、目标效果、分支处理、机制验证结论与开发思路，作为实施的基准；实现未开始。文末 [7. 未决问题](#7-未决问题) 为有意留白的部分。

## 1. 定位与背景

**实时交互是独立于永续会话的功能**：改变 agent 与用户之间的输出通道，不依赖、也不改动记忆机制。

当前交互是"直播式"——content 与 thinking 全部实时流向用户。三个代价：

1. **噪音**：用户被迫阅读中间过程（工具调用、推理碎片），agent 真正想说的话被淹没；
2. **单行道**：agent 工作期间用户无法插话——要么等回合结束，要么执行级取消；执行级取消存在悬空 transcript 缺陷（pygent 0.3.31 未修复，见 `pygent-bugreport-cancel-dangling-tool-call.md`），会话卡死；
3. **不像人**：真正的助手干活时安静、有事说事、被插话会应答，而不是直播思考。

**时机成熟的机制拼图**（均已验证）：per-message checkpoint（fork 快照有稳定基准）、pygent steering 的 detached-result 机制（被打断的工具调用转为后台任务引用，transcript 保持配对）、tool result 边界的 reminder 注入、`GenerationConfig.tool_choice` 具名强制（pygent 四协议适配器齐备）。

## 2. 目标效果

一句话：**agent 埋头干活，唯一对用户可见的输出是 `speak` 调用；用户随时插话，agent 不中断工作也能即时回应；该说话时有人提醒它。**

- agent 的 content 与 thinking 对用户不可见；**journal 照记**——记忆提取与验收依赖完整记录，只是渲染层隐藏（用户侧以"工作中"指示器替代）。
- `speak` 是唯一用户可见通道，工具实现保持**渲染器抽象**：形态（聊天框、气泡）与 source（打字、IM、麦克风）均可插拔，工具实现与通道无关。
- 用户插话零成本：不打断、不等待，agent 边干边答。
- 主动汇报有机制兜底：定期提醒 + 回合收尾兜底。

## 3. 分支矩阵

### 3.1 听（用户消息进入）

| 条件 | 处理 |
|---|---|
| agent 工作中 | fork 主 agent 当前请求状态（**原样复制以维持缓存前缀**，截至最后一个 checkpoint：模型调用中=调用前内容；工具调用中=tool_use 已落盘，以"工具在后台运行"占位）→ 附用户消息原文（不加任何指令）→ 单次模型调用，发言方式按能力声明二选一（见 4.2）→ 回答经 speak 渲染 → 信封注入（见 4.1） |
| agent 空闲 | 正常对话流程，无需 fork；发言仍走 speak |

### 3.2 说（agent 发言）

| 条件 | 处理 |
|---|---|
| 工作中 + 用户发起 | 3.1 的 fork 路径（tool_choice 强制首轮即答） |
| 工作中 + agent 主动 | 后台提醒机制：连续 N 轮未调用 speak → 在 tool result 后附提醒（与信封共用注入点）："有值得告诉用户的信息吗？通过 speak 说" |
| 回合收尾兜底 | 检测最后一轮工具调用是否含 speak——没有则以 user message 形式注入："任务完成，决定说还是不说" → agent 补一次 speak 或静默收尾（代价：收尾可能多一次模型调用，可接受） |
| 空闲 + 唤醒 | 唤醒源**留架构槽位**（接口先定，实现后议）：应用打开自动触发打招呼、定时、事件（agent 可用现有 automation 工具给自己加事件）→ 唤醒后发 system reminder，agent 自行判断说还是不说 |

### 3.3 上下文状态与时序

约定：`ass+tool_call(X)` = assistant 消息携带工具调用；`tool_result(X)` = 工具结果；`▶` 标记运行时状态；`‹…›` 为注入物。content/thinking 全程对用户不可见，时序图中不画。

#### 3.3.1 空闲对话（含收尾兜底）

```
sys
user                       ← agent 空闲，第一条消息：正常对话
ass+tool_call(speak)       ← 空闲发言也走 speak
tool_result(speak 渲染)
ass (纯文本收尾)            ← 用户看不到这段文本 —— 兜底触发条件
user ‹兜底提醒›             ← 引用该文本："要么调 speak 发出（可改措辞），要么 say_nothing=true"
ass+tool_call(speak)       ← 决定说 → 渲染
tool_result(speak 渲染)
或 ass+tool_call(speak(say_nothing=true)) ← 决定不说 → acknowledged (silent)
```

兜底判定（D-RT11）：**按最终回复形态判定**——最终回复是 speak 工具调用（含 say_nothing=true）即视为已决定，不触发兜底；最终回复是纯文本才触发。提醒指向其刚生成的草稿（该文本就在上下文紧邻位置，不重复复制），模型无需重新生成内容。**每回合最多兜底一次**：提醒后仍回纯文本则接受静默结束，防死循环。

#### 3.3.2 工作中插话——fork 点 A：模型调用中

```
主 agent（全程不被打断）：
sys
user (任务)
ass+tool_call(A)
tool_result(A)
▶ 模型调用中——用户消息此刻到达；fork 点 = tool_result(A)（干净边界，无占位）
ass                        ← 在途回答正常落盘，不废弃
ass+tool_call(B)
tool_result(B)
+ ‹信封› <system-reminder>  ← 注入在插话后第一个 tool result 后
     <user-asked>…</user-asked>
     <your-response-in-another-session>…speak 内容…</your-response-in-another-session>
   </system-reminder>
ass+tool_call(C)            ← 主 agent 知情后继续工作

子 agent（单次调用，独立于主 transcript）：
[sys … tool_result(A)]      ← 原样复制（缓存前缀）
+ user ‹用户插话原文›        ← 独立消息，原文直传
→ GenerationConfig(tool_choice="speak")
→ ass+tool_call(speak)      ← 强制发言，参数即回答
→ speak 渲染给用户；子 agent 结束，不落主 transcript
```

**收尾边缘**：若插话后主 agent 已无后续工具调用（回合正在收尾），信封没有注入点——此时信封内容**并入收尾兜底提醒**注入："用户插话已在另一会话回答（原文…）"，主 agent 得知已有人代答，通常选择静默，避免重复回答。

#### 3.3.3 工作中插话——fork 点 B：工具执行中

```
主 agent（全程不被打断）：
…
ass+tool_call(B)            ← checkpoint 已落盘
▶ 工具 B 执行中——用户消息到达；fork 点 = ass+tool_call(B) 之后

子 agent（单次调用）：
[sys … ass+tool_call(B)]    ← 原样复制
+ tool_result(B) ‹占位›      ← "工具在后台运行中"（可附已有部分结果），保持 tool_use/result 配对
+ user ‹用户插话原文›
→ ass+tool_call(speak) → 渲染；子 agent 结束

主 agent 继续：
tool_result(B)               ← B 后台跑完，真实结果正常落盘
+ ‹信封›                     ← 注入于 B 的 result 后
ass+tool_call(C) …
```

#### 3.3.4 主动 speak（N 轮提醒）

```
ass+tool_call(X)
tool_result(X)
+ ‹提醒› 连续 N 轮未 speak    ← 与信封同一注入点
ass+tool_call(speak)
tool_result(speak 渲染)
ass+tool_call(Y)             ← 继续工作
```

#### 3.3.5 空闲唤醒

```
（会话空闲，无运行中执行）
‹唤醒源› 应用打开 / 定时 / 事件 / agent 自建 automation
→ 以 system reminder 开启一个回合
ass+tool_call(speak) 或 ass（静默）   ← agent 自行判断说还是不说
```

## 4. 关键机制与验证结论

### 4.1 信封注入（原"拼接"难点的解法）

对话交换**不改写主 transcript、不打断主 agent**：以结构化信封在主 agent 下一个 tool result 后注入。

```xml
<system-reminder>
  <user-asked>…用户问的…</user-asked>
  <your-response-in-another-session>…子 agent 的回答…</your-response-in-another-session>
</system-reminder>
```

主 agent 跑完当前工具后自然看到"用户问过、我在另一会话答过了"。无 tool_use/tool_result 配对问题、无 checkpoint 手术、无历史改写——原方案中最硬的"拼接"难点由此消解。

### 4.2 子 agent：单次模型调用，两条发言路径

子 agent = **单次模型调用**，不落主 transcript、无写能力——并发问题天然不存在；占位符仅为该次调用的 transcript 合法性服务（引用即可，真实工具结果属于主 agent、照常到达）。

发言方式按模型能力声明**二选一**，互斥、运行时自动选择：

- **主路径**：`GenerationConfig(tool_choice="speak")` 强制调用 speak，回复内容在 speak 参数里。用户消息原文直传，不加任何指令（强制与"勿调工具"指令互斥，不可叠加）。
- **回退路径**：不强制，附"直接回答，勿调工具"指令，模型纯文本回复，harness 将 final answer 按 speak 渲染。

选择依据：模型能力声明中 `tools.choice` 含 `named` → 主路径；否则回退。调用方无感。

**能力门槛现状**（已验证）：

- invoker 门槛（pygent `llm/invoker.py:914-925`）：具名强制要求 `tools.choice` 含 `named`；`response_schema` 要求 `structured_output.json_schema`。
- 本机 glm-5.3-flash（`openai_chat_completions` 协议）当前 `tools.choice` 仅 `['auto']`、`json_schema: false` → 主路径今日不可用，**解锁 = config 该模型条目 `tools.choice` 补 `'named'`**（前提：中转端点实测支持具名 tool_choice——OpenAI 兼容协议标准能力，以真实探针为准）。anthropic_messages 协议下 pygent 内置表本已声明 `named`。
- `response_schema` 对该模型不可用（json_schema=false），设计上也不依赖它。
- 粒度限制（tool_choice 只能"每次都强制"）无伤：子 agent 恰是单次调用；主 agent 不传 tool_choice（默认 auto），行为不变。

### 4.3 中断安全区（为什么 fork 方案顺带绕开了取消缺陷）

| 打断区域 | 现状 |
|---|---|
| 最终文本流式期间打断 | **安全区**：纯模型调用、assistant 消息未落盘，无悬空 tool_use |
| 工具批次期间执行级取消 | **危险区**：`CancelledError` 裸穿 → transcript 悬空 → 后续每轮 400 → 会话卡死（pygent 0.3.31 未修复） |

实时交互设计中**主 agent 永不被执行级取消**：用户插话走 fork 子 agent，主执行继续。取消缺陷的危险区被整个绕开。

## 5. 设计决议记录

1. **D-RT1 信封注入**：对话交换以 reminder 信封注入下一 tool result 后，不做历史拼接、不打断主 agent。
2. **D-RT2 单次调用子 agent**：fork 即单次模型调用；请求原样复制（缓存前缀）；无写能力、无并发写。
3. **D-RT3 发言两路径互斥**：tool_choice 强制（主）/ 软指令 + harness 渲染（回退），按能力声明自动选择；用户消息一律原文直传。
4. **D-RT4 收尾兜底**：回合完成前检测末轮 speak，缺省则注入提醒让 agent 自决。
5. **D-RT5 speak 渲染器抽象**：形态与 source 可插拔（typing/im/voice；聊天框/气泡），工具实现通道无关。
6. **D-RT6 journal 完整性**：渲染层隐藏不减少 journal 记录；记忆与验收的输入不受影响。
7. **D-RT7 空闲唤醒留槽**：只定事件接口，不定实现；首批场景为应用打开打招呼。
8. **D-RT8 与既有机制的关系**：不改动 extractor/builder 记忆机制；不改动 peer 级 agent-message 低权限纪律；提醒注入与既有 reminder 机制共用边界。
9. **D-RT9 落位为独立子包**：新建 `src/lara/runtime/realtime/`（`speak.py` / `fork_reply.py` / `envelope.py` / `nudge.py` / `turn_finalizer.py`），service.py 只留最小接缝（分流入口 + 注入点挂接）。`eternal_conversation.py` 维持单文件现状不动；验收收官后可选拆为 `runtime/eternal/` 包与其对称（独立小整理，届时与 service.py 拆分一并评估）。
10. **D-RT10 speak schema 定案**（修订 D-RT5）：参数 `text`（必填语义，一次性说完）、`attachments`（工作区相对路径数组，运行时校验在 workspace 内后交付渲染器）、`say_nothing`（显式弃言，与 text/attachments 互斥，运行时校验"至少其一"）。不放 `source`（部署属性，运行时配置渲染器，不由模型选）、不放 `kind`（气泡/聊天框由渲染器按时机决定，模型无感）。副作用标记 `EXTERNAL`；tool_result 只回 `"delivered"` / `"acknowledged (silent)"`，不回显全文。收尾兜底因 `say_nothing` 成为确定性闭环：提醒后两种选择都是工具调用，终止干净且留显式决定记录。能力主路径已探针解锁：`hw/GLM-5.3-Flash` 实测支持具名 tool_choice（200 + speak 调用），config `tools.choice` 已补 `'named'`（loader 解析验证）。
11. **D-RT11 收尾兜底按最终回复形态判定**：最终回复是 speak 工具调用（含 `say_nothing=true`，不渲染）= 已决定，不兜底；最终回复是纯文本（用户不可见）才触发兜底——提醒**指向其刚生成的草稿**（该文本就在上下文紧邻位置，不重复复制），agent 调 speak 发出（可改措辞）或 `say_nothing=true` 弃言。**每回合最多兜底一次**，提醒后仍回纯文本则接受静默，防死循环。
12. **D-RT12 桌面双表面，不做隐藏**：现有会话视图保留完整 content/thinking/tool 流（= 工作面，样式后续另行设计），**新增独立的聊天式对话框**只渲染 speak 消息（= 对话面，用户插话入口）。不引入"按会话隐藏渲染"的模式开关。对话框数据通道、入口形态、通知方式见未决问题。

## 6. 开发思路

**Phase 1 — speak 通道（最小可演示）**

0. 落位（D-RT9）：新建 `src/lara/runtime/realtime/` 子包，后续模块全部入包；service.py 不扩容；
1. `speak` 工具：schema 按 D-RT10 定稿（text / attachments / say_nothing），渲染器抽象 + source 参数（运行时配置，不进 schema），先实现 typing → 聊天框；附件经工作区路径校验后交付；
2. 桌面/运行时：assistant content/thinking 对用户流隐藏，"工作中"指示器；journal 不变；
3. 回合收尾兜底（检测 + 注入 + 补一轮）；
4. 中转端点具名 tool_choice 探针 → config `tools.choice` 补 `'named'`。

**Phase 2 — 工作中的实时交互（核心）**

1. fork 快照调用（复制请求、单次调用、tool_choice 强制 + 回退路径）；
2. tool result 边界注入器（信封与提醒共用，对齐现有 reminder 机制）；
3. 用户消息分流：按 execution 状态选择 fork 路径或正常对话；
4. N 轮未 speak 的计数提醒。

**Phase 3 — 空闲唤醒**

1. 唤醒源接口（事件协议定形，实现留空）；
2. 首个场景：应用打开 → 打招呼；
3. agent 自建事件（复用 automation）。

**验证思路**：每阶段单测（fork 快照配对、信封注入位置、收尾检测、能力分派）+ 端到端场景集：工作中插话、连续插话、agent 主动说、收尾兜底、空闲唤醒。

## 7. 未决问题

1. **空闲唤醒事件源**：接口留槽；应用打开、定时、事件的具体协议与实现后议。
2. **speak 对话框的桌面设计**（D-RT12 的展开）：speak 消息的数据通道与历史回放、对话框入口形态（侧栏 tab/抽屉/独立会话）、用户输入与主视图输入的路由关系、agent 主动 speak 时的未读/通知方式。
3. **N 的取值**：多少轮未 speak 触发提醒，按真实使用校准。
4. **回退路径的滥用面**：软指令"勿调工具"在模型不配合时的兜底强度（tool_choice 未解锁期间），必要时先行加 tool_choice 透传探针之外的服务端校验。
