import assert from "node:assert/strict";
import test from "node:test";
import {
  activeSessionNeedsRefresh,
  createActiveSessionSync,
  refreshActiveSession,
} from "./activeSessionSync.js";

function fixture(overrides = {}) {
  const snapshots = [];
  const resumed = [];
  const detail = { session: { session_id: "child", last_case_run_id: "new-run" }, history: [] };
  const trace = { events: [{ id: "new-event" }], context_snapshots: [{ id: "new-context" }] };
  const activity = { events: [{ id: "activity-event" }], events_total: 1, events_truncated: false };
  const controller = new AbortController();
  return {
    snapshots, resumed, detail, trace, activity, controller,
    options: {
      api: {
        getSession: async () => detail,
        getTraceEvents: async () => trace,
        getSessionActivity: async () => activity,
        ...overrides,
      },
      sessionId: "child", scopeId: "conversation", signal: controller.signal,
      isCurrent: () => true,
      onResume: (value) => resumed.push(value),
      onSnapshot: (...value) => snapshots.push(value),
    },
  };
}

test("a background-completed run refreshes trace and context for the open session", async () => {
  const f = fixture();
  let sessionOptions;
  let traceOptions;
  let activityOptions;
  f.options.api.getSession = async (_sessionId, options) => {
    sessionOptions = options;
    return f.detail;
  };
  f.options.api.getTraceEvents = async (sessionId, runId, options) => {
    assert.equal(sessionId, "child");
    assert.equal(runId, "new-run");
    traceOptions = options;
    return f.trace;
  };
  f.options.api.getSessionActivity = async (sessionId, options) => {
    assert.equal(sessionId, "child");
    activityOptions = options;
    return f.activity;
  };
  await refreshActiveSession(f.options);
  assert.equal(sessionOptions.historyLimit, 200);
  assert.equal(traceOptions.eventLimit, 500);
  assert.equal(traceOptions.contextSnapshotLimit, 50);
  assert.equal(activityOptions.eventLimit, 500);
  assert.deepEqual(f.snapshots, [[f.detail, f.trace, f.activity]]);
});

test("a collapsed trace panel skips context snapshot payloads", async () => {
  const f = fixture();
  let traceOptions;
  f.options.api.getTraceEvents = async (_sessionId, _runId, options) => {
    traceOptions = options;
    return f.trace;
  };
  await refreshActiveSession({ ...f.options, includeContextSnapshots: false });
  assert.equal(traceOptions.contextSnapshotLimit, 0);
});

test("an externally started execution attaches to the existing stream", async () => {
  const f = fixture({ getTraceEvents: () => assert.fail("must use the live stream") });
  f.detail.runtime_execution_id = "external-execution";
  await refreshActiveSession(f.options);
  assert.deepEqual(f.resumed, [f.detail]);
  assert.deepEqual(f.snapshots, []);
});

test("an already streaming session does not poll or replace live data", async () => {
  const f = fixture({ getSession: () => assert.fail("already streaming") });
  f.options.isCurrent = () => false;
  await refreshActiveSession(f.options);
  assert.deepEqual(f.snapshots, []);
});

test("a running run without a published execution id is polled until it attaches", async () => {
  const f = fixture({ getTraceEvents: () => assert.fail("must use the live stream") });
  const runningDetail = {
    session: { session_id: "child", last_case_run_id: "new-run", last_case_run_status: "running" },
    history: [],
  };
  const historyLimits = [];
  let call = 0;
  f.options.api.getSession = async (_sessionId, options) => {
    historyLimits.push(options.historyLimit);
    call += 1;
    if (call >= 3) {
      return { ...runningDetail, runtime_execution_id: "live-execution" };
    }
    return runningDetail;
  };
  await refreshActiveSession({ ...f.options, executionIdPollMs: 1 });
  assert.deepEqual(historyLimits, [200, 1, 1, 200]);
  assert.deepEqual(f.resumed.map((detail) => detail.runtime_execution_id), ["live-execution"]);
  assert.deepEqual(f.snapshots, []);
});

test("a run that finishes without publishing an execution id falls back to the snapshot", async () => {
  const f = fixture();
  f.options.api.getSession = async (_sessionId, options) => {
    if (options.historyLimit === 1) {
      return {
        session: { session_id: "child", last_case_run_id: "new-run", last_case_run_status: "passed" },
        history: [],
      };
    }
    return {
      session: { session_id: "child", last_case_run_id: "new-run", last_case_run_status: "running" },
      history: [],
    };
  };
  await refreshActiveSession({ ...f.options, executionIdPollMs: 1 });
  assert.deepEqual(f.resumed, []);
  assert.equal(f.snapshots.length, 1);
});

test("aborting during the execution-id poll stops probing", async () => {
  const f = fixture({ getTraceEvents: () => assert.fail("must not load a stale snapshot") });
  const runningDetail = {
    session: { session_id: "child", last_case_run_id: "new-run", last_case_run_status: "running" },
    history: [],
  };
  let call = 0;
  f.options.api.getSession = async () => {
    call += 1;
    if (call >= 2) f.controller.abort();
    return runningDetail;
  };
  await refreshActiveSession({ ...f.options, executionIdPollMs: 1 });
  assert.deepEqual(f.resumed, []);
  assert.deepEqual(f.snapshots, []);
});

test("switching sessions during detail loading does not attach the old execution", async () => {
  const f = fixture();
  let current = true;
  f.options.isCurrent = () => current;
  f.options.api.getSession = async () => {
    current = false;
    return { ...f.detail, runtime_execution_id: "old-execution" };
  };
  await refreshActiveSession(f.options);
  assert.deepEqual(f.resumed, []);
  assert.deepEqual(f.snapshots, []);
});

test("switching sessions or aborting during trace loading discards the stale snapshot", async () => {
  for (const abort of [false, true]) {
    const f = fixture();
    let current = true;
    f.options.isCurrent = () => current;
    f.options.api.getTraceEvents = async () => {
      if (abort) f.controller.abort();
      else current = false;
      return f.trace;
    };
    await refreshActiveSession(f.options);
    assert.deepEqual(f.snapshots, []);
  }
});

test("a session without a run clears previous trace and keeps session activity", async () => {
  const f = fixture({ getTraceEvents: () => assert.fail("no run") });
  f.detail.session.last_case_run_id = null;
  await refreshActiveSession(f.options);
  assert.deepEqual(f.snapshots[0][2], f.activity);
  assert.deepEqual(f.snapshots[0][1], {
    events: [],
    events_total: 0,
    events_truncated: false,
    context_snapshots: [],
    context_snapshots_total: 0,
    context_snapshots_truncated: false,
  });
});

test("a one-shot refresh reports its outcome to the follow loop", async () => {
  const applied = fixture();
  assert.equal(await refreshActiveSession(applied.options), "applied");

  const attached = fixture();
  attached.detail.runtime_execution_id = "external-execution";
  assert.equal(await refreshActiveSession(attached.options), "attached");

  const dropped = fixture();
  dropped.options.isCurrent = () => false;
  assert.equal(await refreshActiveSession(dropped.options), "dropped");
});

test("the rendered view refreshes only when the lightweight sidebar record changes", () => {
  const current = {
    session_id: "one",
    updated_at: "2026-10-09T10:00:00Z",
    last_case_run_id: "run-one",
    last_case_run_status: "completed",
    model_group_name: "coding",
    selected_model_key: "main",
  };
  assert.equal(activeSessionNeedsRefresh(current, { ...current }), false);
  assert.equal(activeSessionNeedsRefresh(current, {
    ...current,
    updated_at: "2026-10-09T10:01:00Z",
  }), true);
  assert.equal(activeSessionNeedsRefresh(current, {
    ...current,
    session_id: "two",
  }), false);
});

// ---------------------------------------------------------------------------
// createActiveSessionSync: the level-triggered follow loop.
// ---------------------------------------------------------------------------

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function until(condition, timeoutMs = 500) {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("condition not reached in time");
    await wait(1);
  }
}

function sidebarRecord(detail) {
  const session = detail.session;
  return {
    session_id: session.session_id,
    updated_at: session.updated_at,
    last_case_run_id: session.last_case_run_id,
    last_case_run_status: session.last_case_run_status,
    model_group_name: session.model_group_name,
    selected_model_key: session.selected_model_key,
  };
}

function controllerFixture({
  detail,
  pollMs = 1,
  overrides = {},
} = {}) {
  const state = {
    applied: [],
    attached: [],
    sessionCalls: [],
  };
  // `detail` may be a value or a callback so tests can change what the API
  // serves over time; the callback form keeps responses consistent with a
  // record the test advanced in the meantime.
  const sync = createActiveSessionSync({
    api: {
      getSession: async (sessionId) => {
        state.sessionCalls.push(sessionId);
        return typeof detail === "function" ? detail() : detail;
      },
      getTraceEvents: async () => ({
        events: [], events_total: 0, events_truncated: false,
        context_snapshots: [], context_snapshots_total: 0, context_snapshots_truncated: false,
      }),
      getSessionActivity: async () => ({ events: [], events_total: 0, events_truncated: false }),
      ...overrides,
    },
    onAttach: (value) => state.attached.push(value),
    onSnapshot: (...value) => state.applied.push(value),
    executionIdPollMs: pollMs,
  });
  return { sync, state };
}

test("a converged open session stays parked until the sidebar record changes", async (t) => {
  const detail = {
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "passed",
    },
    history: [],
  };
  let served = detail;
  const { sync, state } = controllerFixture({ detail: () => served });
  t.after(() => sync.dispose());
  sync.observeDetail(detail);
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: sidebarRecord(detail),
    streamingIds: {},
  });
  await wait(30);
  assert.equal(state.sessionCalls.length, 0);
  assert.equal(state.applied.length, 0);

  served = { ...detail, session: { ...detail.session, updated_at: "2026-10-26T07:05:00Z" } };
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: sidebarRecord(served),
    streamingIds: {},
  });
  await until(() => state.applied.length === 1);
  assert.deepEqual(state.applied[0][0], served);
  await wait(20);
  assert.equal(state.sessionCalls.length, 1);
});

test("a dropped attempt re-runs at once and applies without a new record", async (t) => {
  const detail = {
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "passed",
    },
    history: [],
  };
  let call = 0;
  const { sync, state } = controllerFixture({
    detail,
    overrides: {
      getSession: async () => {
        call += 1;
        if (call === 1) {
          // The view was taken over mid-attempt (a stream bound and unbound).
          sync.update({ streamingIds: { child: true } });
          setTimeout(() => sync.update({ streamingIds: {} }), 5);
          return detail;
        }
        return detail;
      },
    },
  });
  t.after(() => sync.dispose());
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: sidebarRecord(detail),
    streamingIds: {},
  });
  await until(() => state.applied.length === 1);
  // The first attempt was invalidated, the retry applied; no record change
  // was needed in between.
  assert.equal(call, 2);
});

test("a running record attaches once and parks while the stream is bound", async (t) => {
  const detail = {
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "running",
    },
    runtime_execution_id: "exec-1",
    history: [],
  };
  const { sync, state } = controllerFixture({ detail });
  t.after(() => sync.dispose());
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: sidebarRecord(detail),
    streamingIds: {},
  });
  await until(() => state.attached.length === 1);
  assert.equal(state.attached[0].runtime_execution_id, "exec-1");
  sync.update({ streamingIds: { child: true } });
  await wait(30);
  assert.equal(state.sessionCalls.length, 1);
  assert.equal(state.attached.length, 1);
});

test("attach retries at the paced cadence while no stream binds", async (t) => {
  const detail = () => ({
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "running",
    },
    runtime_execution_id: "exec-1",
    history: [],
  });
  const { sync, state } = controllerFixture({ detail, pollMs: 2 });
  t.after(() => sync.dispose());
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: sidebarRecord(detail()),
    streamingIds: {},
  });
  await until(() => state.attached.length >= 3);
});

test("a run that finishes while probing falls back to the snapshot and converges", async (t) => {
  const runningDetail = {
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "running",
    },
    history: [],
  };
  const finishedDetail = {
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:02:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "passed",
    },
    history: [],
  };
  let call = 0;
  const { sync, state } = controllerFixture({
    overrides: {
      getSession: async () => {
        call += 1;
        return call === 1 ? runningDetail : finishedDetail;
      },
    },
  });
  t.after(() => sync.dispose());
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: sidebarRecord(finishedDetail),
    streamingIds: {},
  });
  await until(() => state.applied.length === 1);
  sync.observeDetail(finishedDetail);
  await wait(20);
  assert.equal(state.attached.length, 0);
});

test("switching sessions drops the in-flight attempt and follows the new record", async (t) => {
  const detailA = {
    session: {
      session_id: "a",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-a",
      last_case_run_status: "passed",
    },
    history: [],
  };
  const detailB = {
    session: {
      session_id: "b",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-b",
      last_case_run_status: "passed",
    },
    history: [],
  };
  const { sync, state } = controllerFixture({
    overrides: {
      getSession: async (sessionId, options) => {
        if (sessionId === "a") {
          // A real fetch dies on abort; the retarget below must unblock the
          // loop so session "b" follows immediately.
          await wait(50);
          if (options.signal.aborted) {
            throw new DOMException("Aborted", "AbortError");
          }
          return detailA;
        }
        return detailB;
      },
    },
  });
  t.after(() => sync.dispose());
  sync.update({
    sessionId: "a",
    scopeId: "conversation",
    listRecord: sidebarRecord(detailA),
    streamingIds: {},
  });
  await wait(5);
  sync.update({
    sessionId: "b",
    scopeId: "conversation",
    listRecord: sidebarRecord(detailB),
    streamingIds: {},
  });
  await until(() => state.applied.length === 1);
  await wait(10);
  assert.deepEqual(state.applied.map(([detail]) => detail.session.session_id), ["b"]);
});

test("dispose stops the loop for good", async (t) => {
  const detail = {
    session: {
      session_id: "child",
      updated_at: "2026-10-26T07:00:00Z",
      last_case_run_id: "run-1",
      last_case_run_status: "passed",
    },
    history: [],
  };
  const { sync, state } = controllerFixture({ detail });
  // A stale view against a moved record gives the loop work to do.
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: { ...sidebarRecord(detail), updated_at: "2026-10-26T07:05:00Z" },
    streamingIds: {},
  });
  await until(() => state.sessionCalls.length >= 1);
  sync.dispose();
  const callsAtDispose = state.sessionCalls.length;
  await wait(20);
  assert.equal(state.sessionCalls.length, callsAtDispose);
  sync.update({
    sessionId: "child",
    scopeId: "conversation",
    listRecord: { ...sidebarRecord(detail), updated_at: "2026-10-26T07:06:00Z" },
    streamingIds: {},
  });
  await wait(20);
  assert.equal(state.sessionCalls.length, callsAtDispose);
});
