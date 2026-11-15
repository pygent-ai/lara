import assert from "node:assert/strict";
import test from "node:test";

import { ACTIVITY_FEED_MAX_EVENTS, EMPTY_ACTIVITY_FEED, applyActivityPage } from "./activityFeed.js";
import { isTelemetryEventKind } from "./eventKinds.js";
import { fetchActivityPage } from "./useActivityFeed.js";

const event = (id, type = "tool.call") => ({ id, type, payload: {} });

test("the initial page replaces the feed and carries the session totals", () => {
  const feed = applyActivityPage(EMPTY_ACTIVITY_FEED, {
    events: [event("a"), event("b")],
    events_total: 10,
    events_truncated: true,
    cursor: "v1:tool_calls.jsonl=40;tool_results.jsonl=80;file_events.jsonl=0;diff_events.jsonl=0",
    tool_calls_total: 5,
    file_events_total: 2,
  });
  assert.deepEqual(feed.events.map((item) => item.id), ["a", "b"]);
  assert.equal(feed.shown, 2);
  assert.equal(feed.total, 10);
  assert.equal(feed.truncated, true);
  assert.equal(feed.toolCallsTotal, 5);
  assert.equal(feed.fileEventsTotal, 2);
  assert.match(feed.cursor, /^v1:/);
});

test("incremental pages append new rows, dedupe re-delivered ones and keep the window flag", () => {
  const initial = applyActivityPage(EMPTY_ACTIVITY_FEED, {
    events: [event("a")], events_total: 3, events_truncated: true, cursor: "c1", tool_calls_total: 2,
  });
  const next = applyActivityPage(initial, {
    events: [event("a"), event("b", "tool.result")], events_total: 4, cursor: "c2", tool_calls_total: 2, file_events_total: 0,
  }, { incremental: true });
  assert.deepEqual(next.events.map((item) => item.id), ["a", "b"]);
  assert.equal(next.cursor, "c2");
  assert.equal(next.total, 4);
  assert.equal(next.truncated, true);

  // An empty page at the same position is a no-op and keeps the identity so
  // memoized consumers do not re-render.
  const idle = applyActivityPage(next, { events: [], events_total: 4, cursor: "c2", tool_calls_total: 2 }, { incremental: true });
  assert.equal(idle, next);
});

test("the feed bounds its rows and reports itself truncated when it drops the oldest", () => {
  const initial = applyActivityPage(EMPTY_ACTIVITY_FEED, { events: [event("a"), event("b")], cursor: "c1" });
  const bounded = applyActivityPage(initial, {
    events: [event("c"), event("d")], cursor: "c2",
  }, { incremental: true, maxEvents: 3 });
  assert.deepEqual(bounded.events.map((item) => item.id), ["b", "c", "d"]);
  assert.equal(bounded.truncated, true);
  assert.equal(bounded.shown, 3);
  assert.ok(ACTIVITY_FEED_MAX_EVENTS > 500);
});

test("telemetry kinds are classified apart from the events a person reads", () => {
  for (const kind of [
    "model.tool_call.delta", "model.text.delta", "model.reasoning.delta",
    "span.started", "span.completed", "effect.executed",
    "model.attempt.started", "model.attempt.succeeded", "model.request.prepared", "model.usage",
    "model.tool_call.started", "tool.authorized", "execution.submitted", "execution.admitted",
    "pygent.v1.context.usage", "lara.transport.keepalive",
  ]) {
    assert.equal(isTelemetryEventKind(kind), true, kind);
  }
  for (const kind of [
    "tool.requested", "tool.started", "tool.completed", "tool.failed", "tool.cancelled",
    "model.tool_call.completed", "model.started", "model.completed", "model.attempt.failed",
    "pygent.v1.runtime.message", "pygent.v1.chat.started", "pygent.v1.approval.requested",
    "execution.started", "execution.completed", "execution.failed", "lara.transport.error",
    "runtime.event", "",
  ]) {
    assert.equal(isTelemetryEventKind(kind), false, kind);
  }
  // Prefix rules must not swallow sibling kinds.
  assert.equal(isTelemetryEventKind("model.attempt.failed"), false);
});

test("fetchActivityPage asks for rows after the cursor and folds them in", async () => {
  const calls = [];
  const api = { getSessionActivity: async (sessionId, options) => {
    calls.push({ sessionId, options });
    return { events: [event("b")], events_total: 2, cursor: "c2", tool_calls_total: 2 };
  } };
  const feed = { ...EMPTY_ACTIVITY_FEED, events: [event("a")], cursor: "c1", shown: 1, total: 1 };
  const next = await fetchActivityPage(api, "s1", feed, { eventLimit: 500 });
  assert.equal(calls[0].options.after, "c1");
  assert.equal(calls[0].options.eventLimit, 500);
  assert.deepEqual(next.events.map((item) => item.id), ["a", "b"]);
  assert.equal(next.cursor, "c2");
});

test("fetchActivityPage reloads the window on a rejected cursor and empties on an unknown session", async () => {
  const calls = [];
  const api = { getSessionActivity: async (_sessionId, options) => {
    calls.push(options.after);
    if (options.after) {
      throw Object.assign(new Error("cursor past end"), { status: 400 });
    }
    return { events: [event("z")], events_total: 1, cursor: "fresh", tool_calls_total: 1 };
  } };
  const feed = { ...EMPTY_ACTIVITY_FEED, events: [event("a")], cursor: "stale", shown: 1, total: 1 };
  const reloaded = await fetchActivityPage(api, "s1", feed);
  assert.deepEqual(calls, ["stale", undefined]);
  assert.deepEqual(reloaded.events.map((item) => item.id), ["z"]);
  assert.equal(reloaded.cursor, "fresh");

  const missing = { getSessionActivity: async () => { throw Object.assign(new Error("gone"), { status: 404 }); } };
  assert.equal(await fetchActivityPage(missing, "s1", feed), EMPTY_ACTIVITY_FEED);

  const failing = { getSessionActivity: async () => { throw Object.assign(new Error("boom"), { status: 500 }); } };
  await assert.rejects(fetchActivityPage(failing, "s1", feed), /boom/);
});
