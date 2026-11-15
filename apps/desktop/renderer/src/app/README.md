# App Shell

React workbench composition, scoped sessions, execution streaming and inspection.

`AssistantActivity` owns the processing header and collapsed state. Tool output
precedes the thinking section; reasoning streams into one expandable preview and
is read from the completed message's `metadata.reasoning_content` after reload.
Run duration comes from the shared `runTiming.js` contract.

When a loaded session exposes `runtime_execution_id`, the workbench resumes that
execution. `run_history_start_index` separates completed history from the active
turn: completed turns remain visible while the active turn is rebuilt from the
execution journal, without duplicating its partially persisted assistant output.
In-memory subscriptions survive switching sessions, and reconnection reports its
own connection state without submitting another user turn.

The inspector's tool and file history is not derived from the live stream: it
follows the session activity feed (see `features/inspector/README.md`), which
polls incrementally while the turn runs. The live stream only overlays in-flight
calls and the raw event list, with telemetry kinds filtered out first.
