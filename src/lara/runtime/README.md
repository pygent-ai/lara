# Runtime

lara's adapter layer over the pygent SDK runtime
(`pygent.agent.pygent_v1.runtime`). The agent graph, prompt pipeline, model
invocation, tool execution, context compression, reminders, realtime (speak,
fork replies, exchange envelopes) and durability all live in the SDK; this
package only adds what the lara product needs on top.

- `service.py`: `LaraRuntimeService(PygentRuntimeService)` — injects lara's
  credential environment (`credentials.env` + keyring) and `load_run_config`
  as the child-config loader, re-registers the SDK speak executor with the
  Feishu delivery channels, and adds the `lara_api` entries the SDK does not
  have: `cancel_turn`, `compress_session`, `execute_case`, task lookups and
  `recovery_case_run`.
- `context.py`: `LaraContext` alias over the SDK `PygentContext` plus
  `LARA_CONTEXT_CODECS` for checkpoint restoration.
- `compression.py`: `SessionCompressionModule`, the manual "compress now"
  entry the SDK does not expose (built from SDK primitives).
- `model_configuration.py`: model discovery, built-in catalogs and
  `lara_credential_environment()`.
- `model_request_journal.py`: per-request journal consumed by the desktop.
- `prompt_profile.py`: the stock Lara prompt profile registered by the
  composition roots (API, CLI); the SDK's prompt registry ships empty.
- `realtime/feishu.py`: Feishu outbound speak channel and inbound bridge
  config. `realtime/nudge.py`: `should_nudge`.
- `reminders/`: re-export of the SDK reminder service.

Model-visible tools are the SDK defaults plus the SDK scheduler tools
(`schedule_task`, `emit_event`); lara registers no tools of its own. Scheduled
and background work is the SDK scheduler's job (`lara scheduler`), and user
messages reach a session only through the SDK's `deliver_user_message`.

## Runtime-state boundary

`PygentAgent` is a reusable, run-independent module graph; each managed
execution receives session/case/run/turn identity, projection, history and
deferred file effects through the context object. Run-bound services
(`EventStore`, `SessionManager`, `DiffTool`) are rebuilt from the context at the
point of use, so concurrent executions stay isolated without a new graph per
turn. Checkpoint restoration delegates to the SDK codec registry; lara owns no
version detection or migration branches.
