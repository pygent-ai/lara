export const SESSION_HISTORY_LIMIT = 200;
export const TRACE_EVENT_LIMIT = 500;
export const CONTEXT_SNAPSHOT_LIMIT = 50;
const EXECUTION_ID_POLL_MS = 2500;

const EMPTY_ACTIVITY = { events: [], events_total: 0, events_truncated: false };

// A running turn publishes runtime_execution_id only once the runtime has
// started it, and context preparation can take tens of seconds. Nothing else
// re-triggers this refresh while the run stays running, so poll lightly and
// attach the moment the id exists instead of leaving the open session
// spinning unattached for the whole turn.
async function waitForRunningExecutionId({
  api, sessionId, scopeId, signal, current, detail, pollMs,
}) {
  while (current()
    && detail.session?.last_case_run_status === "running"
    && !detail.runtime_execution_id) {
    await delay(pollMs, signal);
    if (!current()) return detail;
    detail = await api.getSession(sessionId, {
      scopeId,
      historyLimit: 1,
      signal,
    });
  }
  if (current() && detail.runtime_execution_id) {
    // The resume path rebuilds the transcript from history, so re-read the
    // full detail once before handing it to onResume.
    detail = await api.getSession(sessionId, {
      scopeId,
      historyLimit: SESSION_HISTORY_LIMIT,
      signal,
    });
  }
  return detail;
}

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    let timer = null;
    const finish = () => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    };
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    if (signal?.aborted) {
      onAbort();
      return;
    }
    timer = setTimeout(finish, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function refreshActiveSession({
  api,
  sessionId,
  scopeId,
  signal,
  isCurrent,
  onResume,
  onSnapshot,
  includeContextSnapshots = true,
  executionIdPollMs = EXECUTION_ID_POLL_MS,
}) {
  const current = () => !signal.aborted && isCurrent();
  if (!current()) return;
  let detail = await api.getSession(sessionId, {
    scopeId,
    historyLimit: SESSION_HISTORY_LIMIT,
    signal,
  });
  if (!current()) return;
  if (!detail.runtime_execution_id
    && detail.session?.last_case_run_status === "running") {
    detail = await waitForRunningExecutionId({
      api, sessionId, scopeId, signal, current, detail, pollMs: executionIdPollMs,
    });
    if (!current()) return;
  }
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
          // 0 makes the API skip the snapshot read entirely: every journal
          // snapshot row carries a full request payload.
          contextSnapshotLimit: includeContextSnapshots ? CONTEXT_SNAPSHOT_LIMIT : 0,
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
