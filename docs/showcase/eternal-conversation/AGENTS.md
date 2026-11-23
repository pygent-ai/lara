# AGENTS.md

## 目标

围绕“永续会话”开发时，始终以长期行为连续性为目标：即使前台上下文被替换，Agent 仍应保留完成后续任务所需的事实、约束、决策、进行中工作和冲突依据。

不要把目标降级为复述旧消息、无限扩大上下文窗口或保存聊天摘要。判断实现是否有效，要看 Agent 能否在自然任务中继续正确工作，并在新要求与既有约束冲突时主动确认。

## 开始工作前

- 先阅读以下三份规范，并把它们视为按顺序收敛的约束：
  1. `docs/principles/eternal-conversation-agent.md`
  2. `docs/principles/eternal-conversation-architecture.md`
  3. `docs/principles/eternal-conversation-development.md`
- 再阅读 `docs/guides/eternal-conversation-overview.md` 和与本次改动相关的架构决策。
- 检查工作区状态，保留用户已有修改；不要覆盖、回滚或顺手整理无关文件。
- 修改行为前，先找到该行为的责任主体和现有测试。不要用跨层补丁掩盖归属错误。

## 不可违反的边界

- 保持方案与具体 Agent 框架解耦。把永续会话实现为可挂载到存量 Agent 的 Harness 能力，不要求前台 Agent 为记忆系统重写任务执行逻辑。
- 前台 Agent 只负责完成任务和使用记忆，不负责整理、发布或构建记忆。
- 后台 Agent 负责语义工作：提取或修订 UT、处理冲突、生成 Snapshot，以及把 Pending UT 构建进检索系统。
- Harness 负责机制工作：记录日志、冻结区间、调度后台 Agent、校验结构、推进 cursor、原子发布和替换上下文。不要让 Harness 判断“什么值得记住”或改写记忆语义。
- 只有 Harness 可以改变前台正式上下文。后台 Agent 只能提交提案或通过约定的 memory-cli 写入流程准备状态。
- 以 memory-cli 作为记忆构建和检索契约，不要把它替换成某个 Agent 框架的私有记忆 API。
- 保留完整 Raw History。压缩、Snapshot、UT 和检索索引都不能成为删除原始证据的理由。

## 术语

- 使用 **Working Log** 表示前台系统提示词之外、仍在增长且尚未被正式替换的有序上下文日志。
- 现有代码或旧文档中的 `Working Memory`、`history` 等名称，在没有更具体契约时按 Working Log 理解。不要仅为改名破坏公开 API、持久化字段或兼容性。
- **Snapshot** 是已覆盖历史的有损工作投影，用于让前台迅速恢复“最近发生了什么、正在做什么、接下来做什么”。它不是完整事实库。
- **UT** 是 memory-cli 的可验证记忆单元。
- **Pending** 和 **Built** 都是已发布、可供前台使用的 Long-term Memory。两者在预期语义效果上等价，只在存储表示和检索性能上不同。
- **Raw History** 是独立的按需证据层，不属于 Snapshot，也不依赖 memory-cli 索引。

## 前台上下文契约

按以下顺序组装前台上下文：

1. System Prompt。
2. 固定的 Memory Access Instruction：说明何时及如何调用 memory-cli，以及完整 Raw History 的位置和使用方式。
3. Harness 注入的动态 Snapshot：包含覆盖版本、覆盖 cursor、近期状态和进行中工作。
4. 当前未覆盖的 Working Log。

不要把记忆提取、UT 构建或数据库维护规则写进前台提示词。前台只需要知道如何使用 Snapshot、如何检索详细记忆、何时回看 Raw History，以及发现冲突时如何确认。

当 Snapshot 缺少具体背景、理由、专有名词或组件细节时，要求前台先搜索 memory-cli；当检索结果仍不足或需要逐字证据时，再读取 Raw History。不要因 Snapshot 看起来完整就把它当成事实全集。

## Harness 行为

- 持续追加记录所有新消息、工具调用和任务结果，并使 Raw History 可追溯、可重放、可定位。
- 每次触发整理时，冻结一个明确的 Working Log 区间，并记录起止 cursor、提示词修订和必要的内容标识。冻结后仍允许前台继续产生新的尾部日志。
- 将“已冻结前缀”和“继续增长的尾部”分开处理。后台处理期间不得阻塞前台，也不得把新尾部混入当前提取批次。
- 收到后台 1 号的统一结果后，先校验结构、cursor、覆盖范围和提交状态，再原子发布 Pending UT、Snapshot 与历史覆盖 cursor。
- 发布成功后，用新 Snapshot 替换已覆盖前缀，同时原样保留触发点之后增长的尾部 Working Log。
- 使用幂等提交和持久化检查点。重试、崩溃恢复或重复回调不得造成重复 UT、cursor 回退或多重替换。
- 为每个后台任务记录明确的 job、状态、输入范围和所有权。不要创建无法观察、无法归属或无法恢复的裸异步任务。

用以下时序检查实现是否正确：若旧 Snapshot 覆盖 50 之前的历史，并在消息 89 处触发整理，则后台 1 号使用“旧 Snapshot + 51–89”生成新 Snapshot 和 UT；即使处理期间前台增长到 102，发布后也必须得到“覆盖至 89 的新 Snapshot + 90–102”，不能丢失、重复或提前覆盖尾部。

## 后台 Agent 分工

### 后台 1 号：Fork Extractor

- 从冻结时刻的前台状态 fork，保留前台 System Prompt，并接收未压缩的冻结 Working Log；不要用有损摘要替代提取源。
- 基于旧 Snapshot 和冻结区间提取新增 UT，并在写入前自行与当前已发布记忆和有效 UT 检查冲突。
- 发现冲突时，直接修订已有 UT 或增加必要 UT；被修订的 Built UT 必须重新进入 Pending。
- 在完成 UT 变更后生成新 Snapshot，使其投影旧 Snapshot 与本次冻结区间，而不是只总结本次新增消息。
- 将 UT、Snapshot 和 covered-through cursor 作为同一批次提交。只有这一批次完成并通过 Harness 校验后，前台历史才允许被替换。
- 不要把语义冲突检查转移给 Harness。遗漏概念时，优先修正提取提示词、输入证据或 UT 工作流。

### 后台 2 号：Child Builder

- 作为独立子 Agent 运行，使用可独立配置的模型和干净上下文，不继承前台正在增长的 Working Log。
- 冻结一个 Pending 批次，通过 memory-cli 将其构建进正式检索系统；构建期间允许后台 1 号继续发布新的 Pending UT。
- 构建完成后，只把本批次成功构建的 UT 从 Pending 原子切换为 Built。不要误标构建期间新增的 Pending UT。
- Built 验证只能依赖已构建索引，避免 Pending 内容参与自身构建验证形成“自证循环”。
- 构建失败时保留 Pending 的可用性并记录可恢复状态，不得影响前台继续检索或执行任务。

## memory-cli 动态检索契约

- 为 UT 维护且仅依赖 `pending`、`built` 两个必要状态；没有明确需求时不要提前扩充状态机。
- `memory-cli search` 必须同时返回两层结果：Built UT 走系统检索，Pending UT 以 UT 为单位做字符或字符串匹配。
- 在结果中标明来源状态，使调用者和验收证据能够区分 Pending 与 Built，但不要暗示 Pending 是不可靠的半成品。
- 构建完成后从检索结果中无缝移除对应 Pending 表示，并由 Built 表示接管；不得出现可感知的记忆空窗。
- 同时维护静态和动态使用方式，并在 skill 中明确区分。不要让动态状态要求破坏已有静态 memory-cli 工作流。

## 并发与持久化

- 对前台可见上下文使用快照语义。一次后台任务只能读取其冻结版本，不能边提取边观察变化中的列表。
- 让 extractor 和 builder 使用隔离的运行上下文，避免 ContextVar、消息历史和工具状态在异步任务间串扰。
- 将会话历史追加、检查点和 fork 做成持久化且幂等的操作。复制活跃数据库时使用一致性快照能力，不要直接复制可能与 WAL 不一致的文件集合。
- 对发布、cursor 推进和状态切换使用原子事务。任何中间失败都必须保持“旧正式状态仍可用”。
- 保留前台、后台 1 号、后台 2 号的完整会话记录，以及 Snapshot、UT、构建批次和 Raw History 的关联标识。

## 提示策略

- 同时使用主动提示和被动披露：主动注入固定的使用说明与动态 Snapshot；把详细 UT 和 Raw History 留给 Agent 在需要时检索。
- 在提示词中明确 Snapshot 的有损性和时效边界，提醒 Agent 新上下文可能修正旧 Snapshot。
- 对冲突场景要求检索证据并向用户确认，不要默默让新要求覆盖已知约束。
- 不要把自然任务改造成显式记忆考试。验收问题应口语化，旧信息应隐藏在较早任务里，并通过后续开发决策自然触发回忆。

## 问题定位顺序

发现记忆遗漏、错误回忆或不自然检索时，按以下顺序定位：

1. 检查前台当时收到的 System Prompt、Memory Access Instruction、Snapshot 和 Working Log。
2. 检查前台是否调用 memory-cli、使用了哪些查询，以及为何退回 Bash 或 Raw History。
3. 检查 memory-cli 返回的 Pending/Built UT、排序和状态标记。
4. 检查对应 projection、cursor、冻结区间以及后台 1 号的完整会话。
5. 检查后台 2 号的构建批次、验证结果和状态切换。
6. 最后核对 Raw History，判断问题来自信息未进入提取输入、提取提示不足、检索失败还是前台使用错误。

把修复放在拥有该职责的层。比如后台 1 号漏掉概念，应修正其提示词或输入契约；不要给 Harness 增加语义审核来掩盖问题。

## 验证要求

- 先运行与改动直接相关的单元测试，再运行完整 Python 测试：

  ```powershell
  uv run pytest tests/unit/test_eternal_conversation.py -q
  uv run pytest tests -q
  ```

- 涉及桌面端或会话展示时，再运行：

  ```powershell
  npm --prefix apps/desktop test
  npm --prefix apps/desktop run build
  ```

- 涉及长期连续性时，使用真实模型、真实工具和自然任务运行验收脚本。至少覆盖 200 个复杂任务；扩展到 500 轮时必须单独保存结果，不要用较短测试推断通过。
- 验收必须覆盖：多次上下文替换、Pending 检索、Built 接管、后台构建与前台并发、冲突确认、崩溃或重试恢复、Raw History 回退。
- 不要只用“还记得之前做了什么吗”作为证据。通过后续代码修改、命名沿用、约束冲突和跨任务依赖证明记忆生效。
- 保存前台、两个后台 Agent 的完整会话历史、memory-cli 调用记录、UT 状态变化、Snapshot/cursor 变化和最终任务产物，并指出能够证明连续性的具体位置。
- 不要宣称通过未实际执行的测试；报告命令、结果、失败和当前已知限制。

## 已接受但必须显式保留的权衡

- 当前实现允许后台记忆 Agent 复用前台注册的标准工具，并通过 Bash 调用 memory-cli。把这视为已记录的过渡性选择，不要静默扩大工具权限。
- 若工具调用尚未全部进入统一审批与审计链路，应在相关改动和验收报告中明确指出；不要把该缺口描述成已经解决。
- Pending 与 Built 的实际检索表现可能存在偏移。这是需要持续优化的算法问题，不是给 Pending 降低语义可靠性的理由。

## Git 与交付纪律

- 使用小而聚焦的改动，优先补回归测试，再修改实现。
- 使用 `apply_patch` 编辑手写文件；不要用破坏性 Git 命令，不要清理用户未提交的工作。
- 不要顺手重命名公共字段、改写历史数据或扩大重构范围。涉及迁移时，先写兼容策略和验证方法。
- 提交前检查差异、测试结果和生成物，只提交本任务范围内的文件。
- 最终说明改了什么、为何符合三项原则、验证了什么、证据保存在哪里，以及仍有哪些明确限制。
