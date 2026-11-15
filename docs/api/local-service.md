# Lara Local API

## 启动

```powershell
uv run lara-api --workspace-root E:\Projects\lara --agent dev
```

## Chat execution

启动 execution：

```http
POST /chat/stream
Content-Type: application/json

{"message":"hello","session_id":null,"case_id":"chat"}
```

恢复 execution：

```http
POST /chat/stream
Content-Type: application/json

{"execution_id":"exec-...","after_sequence":12}
```

`message` 与 `execution_id` 必须且只能提供一个；请求中的未知字段会被拒绝。

所有非 keepalive 数据使用同一个 SSE 名称：

```text
event: execution.event
data: {"execution_id":"exec-...","sequence":13,"kind":"model.text.delta","module_path":"lara.react.model","trace_id":"trace-...","data":{"text":"hello"}}
```

客户端应按 `kind` 消费 Pygent 与 Lara Module 发出的事件，并保存最新 `execution_id/sequence` 用于重连。

运行中再发消息使用 `POST /chat/sessions/{session_id}/messages`，请求体为 `{"scope_id":"...","input_id":"客户端生成的唯一 ID","message":"..."}`（可带 `attachments`）。它和 `/chat/stream` 一样走 SDK 的统一入口 `deliver_user_message`，由 SDK 按会话状态路由：

- 「Lara说」实时会话且有回合在运行：fork 当前上下文、单次调用模型回复用户，响应 `{"mode":"interrupt","reply":{"via":"speak","text":...}}`；这段问答封成信封交回运行中的回合，在其下一个步骤边界生效，不打断工作；
- 其他会话：消息排到当前回合之后作为下一轮，响应 `{"mode":"turn"}`；客户端通过会话跟随循环在新回合开始时重新附着到流。

网络重试必须复用同一 `input_id`。会话不存在返回 404，空消息返回 400。`/chat/stream` 在实时会话正在运行时也会走 fork 路径，此时流只包含一条 `pygent.v1.realtime.speak` 事件和 `execution.completed`。

## 审批与后台任务

`GET /runtime/tasks/{task_id}` 保留任务的 `task_id`、`state` 等字段，并返回当前 `output` 与可空的最终 `result`。终态结果存在时优先使用其任务快照和输出；取消结果没有输出时保留已捕获输出。

`DELETE /runtime/tasks/{task_id}` 返回 `cancel_requested`、兼容字段 `cancelled` 和实际 `task` 快照。取消请求获接收不等于进程清理已确认，须检查任务状态；任务已结束时返回 `false` 和终态快照，任务不存在时返回 404。

Bash 前台等待到期后，任务仍运行，原回合的事件流不会追加后台终态。客户端可按任务 ID 查询，或重新读取会话审计。普通回合完成不会将后台任务改为成功。

```http
POST /chat/approvals/{approval_id}
{"approved":true,"comment":"approved"}

GET /runtime/tasks/{task_id}
DELETE /runtime/tasks/{task_id}
```

## 定时任务（SDK scheduler）

`/scheduler/*` 是 pygent SDK 调度器在当前工作区的 HTTP 面；写操作都是签名的 `user` submission，由 API 进程内的调度守护线程下次轮询时提升（响应 202，返回 submission 而非任务）。

```http
GET  /scheduler/tasks?status=approved|needs_review|paused|rejected
POST /scheduler/tasks            {"name":"...","schedule":"daily 09:00","prompt":"...","instructions":"","session":null,"priority":"normal"}
GET  /scheduler/tasks/{task_id}  -> {"task":{...},"runs":[...]}
POST /scheduler/tasks/{task_id}/{approve|reject|pause|resume|delete}
GET  /scheduler/runs?task_id=
POST /scheduler/events           {"event":"deploy-finished","data":{}}
```

调度表达式为 `every <n>[smhd]`、`at <ISO>`、`daily HH:MM`、`on <event>`。模型通过 `schedule_task`/`emit_event` 工具提交的任务默认 `needs_review`，在桌面"定时任务"页或 `lara scheduler approve` 批准。详见 [`lara scheduler`](../cli/lara-scheduler.md)。

## Settings

`GET /settings` 返回用户级 agent profile 及 routes，不返回原始 API key。`PATCH /settings` 可以切换 workspace、agent、步数和 context window。模型在 `~/.lara/config.yaml` 中声明并由所有项目共用。
