import test from "node:test";
import assert from "node:assert/strict";

import { createSessionTranscripts } from "./sessionTranscripts.js";

const history = [{ id: "persisted", role: "assistant", content: "Saved answer" }];
const live = [
  { id: "user-live", role: "user", content: "Keep working" },
  { id: "assistant-live", role: "assistant", content: "Still working", status: "running" },
];

test("a live transcript wins over history until the turn releases it", () => {
  const store = createSessionTranscripts();
  store.setHistory("s1", history);
  assert.equal(store.messages("s1"), history);

  store.setLive("s1", live);
  assert.equal(store.messages("s1"), live);
  assert.equal(store.hasLive("s1"), true);

  // History refreshed by a poll lands behind the live transcript.
  const newer = [...history, { id: "later", role: "user", content: "more" }];
  store.setHistory("s1", newer);
  assert.equal(store.messages("s1"), live);

  store.clearLive("s1");
  assert.equal(store.messages("s1"), newer);
  assert.equal(store.hasLive("s1"), false);
});

test("unknown sessions read as the same empty transcript", () => {
  const store = createSessionTranscripts();
  assert.equal(store.messages("missing"), store.messages("other"));
  assert.deepEqual(store.messages("missing"), []);
  store.clearLive("missing");
  store.delete("missing");
});

test("updateLive applies the updater once and keeps streaming while the session is off screen", () => {
  const store = createSessionTranscripts();
  let calls = 0;
  const append = (text) => (items) => {
    calls += 1;
    return items.map((item) => (item.id === "assistant-live" ? { ...item, content: `${item.content}${text}` } : item));
  };

  store.setLive("s1", live);
  // The user switches to another session: nothing here depends on the view.
  store.setHistory("s2", history);
  store.updateLive("s1", append("!"));
  store.updateLive("s1", append("?"));
  assert.equal(calls, 2);
  assert.equal(store.messages("s1")[1].content, "Still working!?");
  assert.equal(store.messages("s2"), history);
});

test("updateLive seeds from history and skips the write when nothing changed", () => {
  const store = createSessionTranscripts();
  store.setHistory("s1", history);
  let notified = 0;
  store.subscribe(() => { notified += 1; });

  assert.equal(store.updateLive("s1", (items) => items), history);
  assert.equal(store.hasLive("s1"), false);
  assert.equal(notified, 0);

  const next = store.updateLive("s1", (items) => [...items, live[0]]);
  assert.equal(store.messages("s1"), next);
  assert.equal(next[0], history[0]);
  assert.equal(store.hasLive("s1"), true);
  assert.equal(notified, 1);
});

test("silent writes defer the notification until flushed", () => {
  const store = createSessionTranscripts();
  let notified = 0;
  store.subscribe(() => { notified += 1; });

  store.setLive("s1", live, { silent: true });
  store.updateLive("s1", (items) => [...items], { silent: true });
  assert.equal(notified, 0);
  store.notify();
  assert.equal(notified, 1);
});

test("subscribers stop receiving notifications after unsubscribing", () => {
  const store = createSessionTranscripts();
  let notified = 0;
  const unsubscribe = store.subscribe(() => { notified += 1; });
  store.setHistory("s1", history);
  unsubscribe();
  store.setHistory("s1", live);
  store.delete("s1");
  assert.equal(notified, 1);
  assert.deepEqual(store.messages("s1"), []);
});

test("the cache evicts the least recently written idle session but never a live one", () => {
  const store = createSessionTranscripts({ limit: 2 });
  store.setLive("running", live);
  store.setHistory("one", history);
  store.setHistory("two", history);
  store.setHistory("three", history);
  assert.deepEqual(store.messages("one"), []);
  assert.equal(store.messages("running"), live);
  assert.equal(store.messages("two"), history);
  assert.equal(store.messages("three"), history);

  // A re-write refreshes recency.
  store.setHistory("two", history);
  store.setHistory("four", history);
  assert.equal(store.messages("two"), history);
  assert.deepEqual(store.messages("three"), []);
  assert.equal(store.messages("running"), live);
});

test("committing a finished turn keeps its transcript in place of the older history", () => {
  const store = createSessionTranscripts();
  store.setHistory("s1", history);
  store.setLive("s1", live);
  store.commitLive("s1");
  assert.equal(store.messages("s1"), live);
  assert.equal(store.hasLive("s1"), false);

  // The reload then replaces it with persisted rows.
  store.setHistory("s1", history);
  assert.equal(store.messages("s1"), history);

  let notified = 0;
  store.subscribe(() => { notified += 1; });
  store.commitLive("s1");
  assert.equal(notified, 0);
});

test("an unbound new-chat stream moves under its session once named", () => {
  const store = createSessionTranscripts();
  store.setLive("", live);
  store.setLive("session-1", store.live(""));
  store.clearLive("");
  assert.equal(store.messages("session-1"), live);
  assert.deepEqual(store.messages(""), []);
});
