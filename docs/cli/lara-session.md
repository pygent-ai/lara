# Lara Session CLI

## 单轮执行

```powershell
uv run lara session run --new --message "检查当前项目"
uv run lara --agent dev session run --new --message "运行测试并总结失败"
uv run lara session run <session_id> --message "继续处理"
uv run lara session run scheduler --message "按计划检查"   # 不存在的命名会话会被创建
```

命令返回稳定的 JSON 结果，包括 `session_id`、`case_run_id`、
`execution_id`、`status`、`final_answer` 和 `run_dir`。

`session run <session_id>` 对不存在的 id 会以该 id 创建一个 chat 会话：调度器
（见 [`lara scheduler`](lara-scheduler.md)）用稳定的会话名运行任务，依赖这一点。

## 交互模式

```powershell
uv run lara session chat --new
uv run lara session chat <session_id>
```

交互模式直接订阅 Pygent execution journal：

- `model.text.delta` 输出正文；
- `pygent.v1.approval.requested` 触发终端审批；
- execution 完成、失败或取消由 Pygent Runtime 管理。

模型通过用户级 `~/.lara/config.yaml` 中的 `agents[]` 配置，所有项目共用。
CLI 不提供 `--model`。

`session run` 默认拒绝需要交互审批的高风险工具，除非工具名位于
`runtime.approvals.preauthorized_tools`。`session chat` 可以在终端中完成审批。

## 会话管理

```powershell
uv run lara session list
uv run lara session show <session_id>
uv run lara session create --case <case_id> --mode chat
```

## 消息投递模型

CLI、HTTP API 和桌面端都通过统一入口 `LocalExecutionHost.deliver_user_message`
投递用户消息（lara 在 SDK 路由前加了一条普通会话的 steering 路由）：

- 「Lara说」实时会话且有回合正在运行：消息 **fork** 当前上下文，单次调用模型
  强制回复用户（speak），这段问答封成信封交回正在运行的回合，在它下一个步骤
  边界生效，不打断工作；
- 普通会话且有回合正在运行：消息作为 **steering** 注入正在运行的执行
  （`runtime.steering.mode` 默认 `immediate`：打断当前步骤，作为下一次模型输入），
  可带附件；
- 其他情况（空闲会话、新会话、运行中回合无法接收输入）：开始或排队一个真实回合。

CLI 进程里没有正在运行的回合，所以 `session run`/`chat` 的每条输入都是一个
回合。跨会话的 Agent 消息（旧 `session send/start/fork/wait`）和模型侧的
`agent_*` 协作工具已移除；后台任务统一走 `lara scheduler`。
