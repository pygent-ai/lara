# Sessions

`SessionManager(SDK SessionManager)`: the SDK owns creation, loading, history
checkpoints, forks and case runs; lara adds the product-level queries the API
and CLI read (run timing, history windows, run owner, model selection changes)
and `ensure(session_id)`, which creates a session under a stable name for the
scheduler.

Project sessions live below `~/.lara/projects/<canonical-path-hash>/sessions`;
projectless sessions use `~/.lara/conversations/sessions`. Runtime databases and
project skills share the same data root. Creating a session does not create a
`.lara` directory inside the workspace.

Starting a case records its history boundary and publishes the running case ID
in session metadata. Native assistant messages preserve usage counters and
reasoning display metadata across checkpoints and application restarts.

`LARA_SAYS_SESSION_MODE` (= the SDK's `pygent.v1-says`) marks the one session
with realtime interaction; `realtime_enabled_for(session)` is the SDK gate.
There is no cross-session message inbox or session directory any more: agent
collaboration was removed together with the SDK's, and background work goes
through the scheduler.
