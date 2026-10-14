import assert from "node:assert/strict";
import test from "node:test";
import { refreshActiveSession } from "./activeSessionSync.js";

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
