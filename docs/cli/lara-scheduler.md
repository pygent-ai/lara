# `lara scheduler`

`lara scheduler` is the command face of the pygent SDK scheduler
(`pygent.agent.pygent_v1.scheduler`) for one workspace. In a source checkout,
invoke it as `uv run lara scheduler`. Every write is a signed `user`
submission that the daemon verifies and promotes on its next poll; reads come
straight from the SDK task/run tables under
`<project pygent_root>/scheduler/pygent.v1-scheduler.sqlite3`.

## The daemon

```powershell
uv run lara scheduler run            # foreground poll loop
uv run lara scheduler run --once     # one poll, for scripts
```

The desktop/API process runs the same daemon on a background thread while it
is alive, so tasks fire whenever Lara is open. Each run is one
`lara --workspace-root <ws> session run <task.session> -m <task-context prompt>`
child process; a task's session is created under that name on first use.

Signing keys live under `~/.lara/scheduler/keys/`; the daemon creates them on
first start (`lara scheduler init-keys [--force]` rotates them).

## Tasks

```powershell
uv run lara scheduler add --name "nightly regression" --schedule "daily 02:00" --prompt "Run the regression suite and summarize failures"
uv run lara scheduler add --name "poll" --schedule "every 30m" --prompt-file task.md --session ops --priority low
uv run lara scheduler add --name "post-deploy" --schedule "on deploy-finished" --prompt "Smoke-test the new build"
uv run lara scheduler add --name "once" --schedule "at 2026-12-01T09:00:00+08:00" --prompt "Send the monthly report"

uv run lara scheduler list [--status approved|needs_review|paused|rejected]
uv run lara scheduler show <task_id>          # task + its runs
uv run lara scheduler runs [--task <task_id>]
uv run lara scheduler approve|reject|pause|resume|delete <task_id>
uv run lara scheduler emit deploy-finished --data '{"build": "1.2.3"}'
```

Schedules are the SDK's four forms: `every <n>[smhd]`, `at <ISO>`,
`daily HH:MM`, `on <event>`.

## Trust boundary

User submissions (CLI, `/scheduler` HTTP routes, the desktop page) are
approved on promotion. The model's `schedule_task` / `emit_event` tools submit
as `agent` and land in `needs_review` unless `scheduler.agent_create_enabled`
is set in the run config; approve them from the desktop "定时任务" page or with
`lara scheduler approve <task_id>`. Agent requests for `high` priority are
normalized to `normal`.

## HTTP

`GET /scheduler/tasks[?status=]`, `POST /scheduler/tasks` (202, returns the
submission), `GET /scheduler/tasks/{id}` (task + runs),
`POST /scheduler/tasks/{id}/{approve|reject|pause|resume|delete}` (202),
`GET /scheduler/runs[?task_id=]`, `POST /scheduler/events`.
