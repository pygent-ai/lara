# Orchestration

lara's thin layer over `pygent.agent.pygent_v1.orchestration`. The SDK owns turn
commands, managed turns, session lanes, runtime leases/pools and the unified
user-message entry (`deliver_user_message`: fork interrupt for the realtime
session with a running turn, otherwise a queued turn). This package only adds
what `lara_api` and the CLI need on top:

- `execution_host.py`: `LocalExecutionHost(SDK host)` that builds
  `LaraRuntimeService` (Feishu speak channel, lara_api entries) with the SDK's
  fork-exchange hook wired, and pins lara's `SessionManager` to every runtime
  generation.
- `session_execution.py`: `SessionExecutionCoordinator(SDK coordinator)` plus
  execution-id / case-run-id indexes and durable recovery after a restart.
- `runtime_pool.py`: `WorkspaceRuntimePool(SDK pool)` plus eager scope retirement
  for `ApiContext.areload`.
- `session_turns.py`: `SessionTurnService` — turn submission with lara's resume
  kinds (`message_kind` / `message_data`), used by the resume path only.

Session identity includes the resolved sessions root as well as the session ID,
so equal IDs in different desktop project scopes never share a lane.

Background work (scheduled tasks, agent-started sessions) is not orchestrated
here: it goes through the SDK scheduler (`pygent.agent.pygent_v1.scheduler`),
whose daemon runs each task as a `lara session run` child process.
