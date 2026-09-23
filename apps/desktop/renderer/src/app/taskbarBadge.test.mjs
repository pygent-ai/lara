import assert from "node:assert/strict";
import test from "node:test";

import {
  countUnacknowledgedCompletedSessions,
  isUnacknowledgedCompletedSession,
  renderBadgeDataUrl,
  updateTaskbarBadge,
} from "./taskbarBadge.js";

test("finished sessions waiting to be opened are counted", () => {
  const finished = { session_id: "s1", last_case_run_status: "passed", updated_at: "2026-09-01T10:00:00Z" };
  const failed = { session_id: "s2", last_case_run_status: "failed", updated_at: "2026-09-01T10:01:00Z" };

  assert.equal(isUnacknowledgedCompletedSession(finished, {}), true);
  assert.equal(isUnacknowledgedCompletedSession(failed, {}), true);
  assert.equal(countUnacknowledgedCompletedSessions([{ sessions: [finished, failed] }], {}), 2);
});

test("running, never-run, and already opened sessions are excluded", () => {
  const running = { session_id: "s1", last_case_run_status: "running", updated_at: "2026-09-01T10:00:00Z" };
  const neverRan = { session_id: "s2", last_case_run_status: "", updated_at: "2026-09-01T10:00:00Z" };
  const opened = { session_id: "s3", last_case_run_status: "passed", updated_at: "2026-09-01T10:00:00Z" };

  assert.equal(isUnacknowledgedCompletedSession(running, {}), false);
  assert.equal(isUnacknowledgedCompletedSession(neverRan, {}), false);
  assert.equal(
    isUnacknowledgedCompletedSession(opened, { s3: "passed:2026-09-01T10:00:00Z" }),
    false,
  );
});

test("a new run re-flags a previously opened session", () => {
  const earlier = { session_id: "s1", last_case_run_status: "passed", updated_at: "2026-09-01T10:00:00Z" };
  const rerun = { ...earlier, updated_at: "2026-09-01T10:05:00Z" };

  assert.equal(isUnacknowledgedCompletedSession(rerun, { s1: "passed:2026-09-01T10:00:00Z" }), true);
});

test("missing groups and sessions are tolerated", () => {
  assert.equal(countUnacknowledgedCompletedSessions(undefined, {}), 0);
  assert.equal(countUnacknowledgedCompletedSessions([null, {}, { sessions: [null] }], undefined), 0);
});

test("badge renders the count into a png data url, capped at 9+", () => {
  const { context, doc } = createStubDocument();
  const dataUrl = renderBadgeDataUrl(3, doc);

  assert.match(dataUrl, /^data:image\/png;base64,/);
  assert.ok(context.calls.some((call) => call[0] === "fillText" && call[1] === "3"));
  assert.equal(renderBadgeDataUrl(0, doc), null);
  assert.equal(renderBadgeDataUrl(3, null), null);
});

test("badge counts above nine render as 9+", () => {
  const { context, doc } = createStubDocument();

  renderBadgeDataUrl(12, doc);

  assert.ok(context.calls.some((call) => call[0] === "fillText" && call[1] === "9+"));
});

test("badge updates go through the desktop bridge", () => {
  const sent = [];
  const desktop = { setBadgeCount: (count, dataUrl) => sent.push([count, dataUrl]) };

  assert.equal(updateTaskbarBadge(0, desktop, createStubDocument().doc), true);
  assert.deepEqual(sent, [[0, null]]);

  const doc = createStubDocument();
  assert.equal(updateTaskbarBadge(2, desktop, doc.doc), true);
  assert.equal(sent[1][0], 2);
  assert.match(sent[1][1], /^data:image\/png;base64,/);
});

test("badge updates are skipped without the desktop bridge or a canvas", () => {
  assert.equal(updateTaskbarBadge(1, undefined, createStubDocument().doc), false);

  const sent = [];
  const desktop = { setBadgeCount: (count, dataUrl) => sent.push([count, dataUrl]) };
  const doc = createStubDocument();
  doc.doc.createElement = () => ({ width: 0, height: 0, getContext: () => null });

  assert.equal(updateTaskbarBadge(1, desktop, doc.doc), false);
  assert.deepEqual(sent, []);
});

function createStubDocument() {
  const context = {
    calls: [],
    beginPath: (...args) => context.calls.push(["beginPath", ...args]),
    arc: (...args) => context.calls.push(["arc", ...args]),
    fill: () => context.calls.push(["fill"]),
    fillText: (label, ...args) => context.calls.push(["fillText", label, ...args]),
  };
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => context,
    toDataURL: (type) => `data:image/png;base64,${type}`,
  };
  return {
    context,
    doc: {
      createElement: (tag) => {
        context.calls.push(["createElement", tag]);
        return canvas;
      },
    },
  };
}
