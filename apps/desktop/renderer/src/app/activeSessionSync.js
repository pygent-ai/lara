export const SESSION_HISTORY_LIMIT = 200;
export const TRACE_EVENT_LIMIT = 500;
export const CONTEXT_SNAPSHOT_LIMIT = 50;
const EXECUTION_ID_POLL_MS = 2500;

const EMPTY_ACTIVITY = { events: [], events_total: 0, events_truncated: false };
const EMPTY_TRACE = {
  events: [],
  events_total: 0,
  events_truncated: false,
  context_snapshots: [],
  context_snapshots_total: 0,
  context_snapshots_truncated: false,
};

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

// One synchronization attempt for the open session. The outcome is explicit
// because the caller needs it: "dropped" means the attempt was invalidated
// before anything was applied (session switch, abort, or the view was taken
// over by a live stream), and the follow loop must re-run instead of waiting
// for a list change that may never come.
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
  if (!current()) return "dropped";
  let detail = await api.getSession(sessionId, {
    scopeId,
    historyLimit: SESSION_HISTORY_LIMIT,
    signal,
  });
  if (!current()) return "dropped";
  if (!detail.runtime_execution_id
    && detail.session?.last_case_run_status === "running") {
    detail = await waitForRunningExecutionId({
      api, sessionId, scopeId, signal, current, detail, pollMs: executionIdPollMs,
    });
    if (!current()) return "dropped";
  }
  if (detail.runtime_execution_id) {
    onResume(detail);
    return "attached";
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
      : Promise.resolve(EMPTY_TRACE),
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
  if (current()) {
    onSnapshot(detail, trace, activity);
    return "applied";
  }
  return "dropped";
}

export function sessionFreshnessSignature(session) {
  if (!session?.session_id) return "";
  return [
    session.session_id,
    session.updated_at || "",
    session.last_case_run_id || "",
    session.last_case_run_status || "",
    session.model_group_name || "",
    session.selected_model_key || "",
  ].join("|");
}

export function activeSessionNeedsRefresh(viewSession, listRecord) {
  return Boolean(
    viewSession?.session_id
    && viewSession.session_id === listRecord?.session_id
    && sessionFreshnessSignature(viewSession) !== sessionFreshnessSignature(listRecord)
  );
}

export function activeSessionRunAwaitingAttach(viewSession, listRecord, streamingIds) {
  // A scheduler-driven turn leaves the list record running while this client
  // has no live stream for the session: the execution id is published only
  // after the runtime starts the run, and a failed attach also drops the
  // streaming flag. Keep probing so the session follows the live turn instead
  // of showing an unattachable spinner.
  return Boolean(
    viewSession?.session_id
    && viewSession.session_id === listRecord?.session_id
    && listRecord?.last_case_run_status === "running"
    && !streamingIds?.[viewSession.session_id]
  );
}

// Follows the open session with a single level-triggered loop. App state
// changes arrive through update() / observeDetail() and wake the loop; the
// loop re-evaluates convergence against the sidebar record and re-runs a
// dropped attempt immediately, so a lost refresh can never leave the
// transcript frozen until some unrelated state change happens to retrigger it.
//
//   converged:  the rendered detail matches the sidebar record and no run is
//               waiting to be attached   -> park until the next wake
//   working:    one refreshActiveSession attempt per pass
//   re-active:  "dropped" retries at once; attach retries and convergence
//               re-checks are paced with executionIdPollMs
export function createActiveSessionSync({
  api,
  onAttach,
  onSnapshot,
  includeContextSnapshots = () => true,
  executionIdPollMs = EXECUTION_ID_POLL_MS,
}) {
  let disposed = false;
  let inputs = { sessionId: "", scopeId: "", listRecord: null, streamingIds: {} };
  // The detail the view last rendered (via observeDetail) or this loop last
  // applied; convergence is measured between it and the sidebar record.
  const viewDetails = new Map();
  let fetchAbort = null;
  let wakeup = null;
  let loopTask = null;

  function wake() {
    const pending = wakeup;
    wakeup = null;
    pending?.();
  }

  function idle(ms) {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        wakeup = null;
        if (timer !== null) clearTimeout(timer);
        resolve();
      };
      wakeup = finish;
      const timer = ms == null ? null : setTimeout(finish, ms);
    });
  }

  function isCurrent(sessionId) {
    return !disposed
      && inputs.sessionId === sessionId
      && !inputs.streamingIds?.[sessionId];
  }

  function converged(record, sessionId) {
    const view = viewDetails.get(sessionId);
    if (!view) return false;
    if (activeSessionNeedsRefresh(view.session, record)) return false;
    return !activeSessionRunAwaitingAttach(view.session, record, inputs.streamingIds);
  }

  async function run() {
    while (!disposed) {
      const { sessionId } = inputs;
      const record = inputs.listRecord;
      if (!sessionId || !record || record.session_id !== sessionId) {
        await idle();
        continue;
      }
      // A bound live stream owns the transcript until it ends; the post-turn
      // reload and the next record change wake the loop.
      if (inputs.streamingIds?.[sessionId]) {
        await idle();
        continue;
      }
      if (converged(record, sessionId)) {
        await idle();
        continue;
      }
      let outcome = "dropped";
      try {
        outcome = await refreshActiveSession({
          api,
          sessionId,
          scopeId: inputs.scopeId,
          signal: fetchAbort.signal,
          isCurrent: () => isCurrent(sessionId),
          onResume: (detail) => {
            viewDetails.set(sessionId, detail);
            onAttach(detail);
          },
          onSnapshot: (detail, trace, activity) => {
            viewDetails.set(sessionId, detail);
            onSnapshot(detail, trace, activity);
          },
          includeContextSnapshots: includeContextSnapshots(),
          executionIdPollMs,
        });
      } catch {
        // Transient failures (network, a session deleted mid-poll, an aborted
        // retarget) must not kill the loop: a retarget re-reads inputs at
        // once, a same-target failure retests at the paced cadence.
        if (disposed) return;
        if (inputs.sessionId !== sessionId) continue;
        await idle(executionIdPollMs);
        continue;
      }
      if (disposed) return;
      // A dropped attempt must not retry as a bare microtask: when the drop
      // reason (for example a streaming flag) outlives the attempt, a
      // microtask retry would spin without ever letting timers run. Pacing
      // every wait on the wake channel keeps the self-heal immediate for real
      // input changes while bounding same-input retries.
      if (inputs.sessionId !== sessionId) continue;
      await idle(executionIdPollMs);
    }
  }

  function invalidateAttempt() {
    // The view was re-rendered authoritatively (session switch or a fresh
    // load): an in-flight attempt is racing it, so abort the attempt and arm
    // a fresh controller for the next pass.
    fetchAbort?.abort();
    fetchAbort = new AbortController();
  }

  return {
    update(next) {
      const previousSessionId = inputs.sessionId;
      inputs = { ...inputs, ...next };
      if (inputs.sessionId !== previousSessionId) {
        invalidateAttempt();
      }
      if (!loopTask && !disposed) {
        fetchAbort = new AbortController();
        loopTask = run();
      }
      wake();
    },
    // The view rendered this detail (session switch, post-turn reload); the
    // loop treats it as the current convergence baseline.
    observeDetail(detail) {
      const sessionId = detail?.session?.session_id;
      if (!sessionId) return;
      viewDetails.set(sessionId, detail);
      invalidateAttempt();
      wake();
    },
    dispose() {
      disposed = true;
      fetchAbort?.abort();
      wake();
    },
  };
}
