export const SESSION_HISTORY_LIMIT = 200;
export const TRACE_EVENT_LIMIT = 500;
export const CONTEXT_SNAPSHOT_LIMIT = 50;

const EMPTY_ACTIVITY = { events: [], events_total: 0, events_truncated: false };

export async function refreshActiveSession({ api, sessionId, scopeId, signal, isCurrent, onResume, onSnapshot }) {
  const current = () => !signal.aborted && isCurrent();
  if (!current()) return;
  const detail = await api.getSession(sessionId, {
    scopeId,
    historyLimit: SESSION_HISTORY_LIMIT,
    signal,
  });
  if (!current()) return;
  if (detail.runtime_execution_id) {
    onResume(detail);
    return;
  }
  const runId = detail.session.last_case_run_id;
  // The run trace is the raw-event window for one turn; the session activity
  // feed aggregates tool and file history across every run of the session.
  const [trace, activity] = await Promise.all([
    runId
      ? api.getTraceEvents(sessionId, runId, {
          eventLimit: TRACE_EVENT_LIMIT,
          contextSnapshotLimit: CONTEXT_SNAPSHOT_LIMIT,
          signal,
        })
      : Promise.resolve({
          events: [],
          events_total: 0,
          events_truncated: false,
          context_snapshots: [],
          context_snapshots_total: 0,
          context_snapshots_truncated: false,
        }),
    api
      .getSessionActivity(sessionId, { eventLimit: TRACE_EVENT_LIMIT, signal })
      .catch((err) => {
        // A turn that just started has no run directory yet; the periodic
        // refresh retries, so a missing trace is not a user-facing error.
        if (err?.status === 404) {
          return EMPTY_ACTIVITY;
        }
        throw err;
      }),
  ]);
  if (current()) onSnapshot(detail, trace, activity);
}
