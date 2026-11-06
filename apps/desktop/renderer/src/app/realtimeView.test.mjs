import assert from "node:assert/strict";
import { test } from "node:test";

import {
  displayRowsForView,
  extractSpeaksFromTurn,
  isInternalMessage,
  isLaraSaysSession,
  LARA_SAYS_SESSION_MODE,
  loadViewMode,
  normalizeViewMode,
  storeViewMode,
} from "./realtimeView.js";

function withStorage(fn) {
  const store = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, value),
    },
  };
  try {
    fn(store);
  } finally {
    delete globalThis.window;
  }
}

test("normalizeViewMode defaults unknown modes to front", () => {
  assert.equal(normalizeViewMode("back"), "back");
  assert.equal(normalizeViewMode("front"), "front");
  assert.equal(normalizeViewMode(undefined), "front");
  assert.equal(normalizeViewMode("nonsense"), "front");
});

test("view mode persists per session", () => {
  withStorage(() => {
    storeViewMode("s1", "back");
    storeViewMode("s2", "weird");
    assert.equal(loadViewMode("s1"), "back");
    assert.equal(loadViewMode("s2"), "front");
    assert.equal(loadViewMode("missing"), "front");
  });
});

test("finalizer reminders are internal in both views", () => {
  const row = { id: "r1", role: "user", kind: "lara.realtime.finalizer", content: "提醒" };
  assert.equal(isInternalMessage(row), true);
  assert.equal(displayRowsForView([row], "front").length, 0);
  assert.equal(displayRowsForView([row], "back").length, 0);
});

test("extractSpeaksFromTurn collects every speak call in order", () => {
  const turn = [
    { role: "user", content: "first" },
    {
      role: "assistant",
      tool_calls: [{ name: "speak", arguments: { text: "第一次" } }],
    },
    { role: "tool", content: "delivered" },
    {
      role: "assistant",
      content: "",
      tool_calls: [{ name: "speak", arguments: { text: "最终的说法" } }],
    },
  ];
  const speaks = extractSpeaksFromTurn(turn);
  assert.deepEqual(
    speaks.map((speak) => speak.text),
    ["第一次", "最终的说法"],
  );
  assert.equal(speaks[1].sayNothing, false);
});

test("extractSpeaksFromTurn keeps delivered speaks ahead of a trailing say_nothing", () => {
  // The finalizer's bottom-line decision must not erase the replies the turn
  // already delivered.
  const turn = [
    { role: "assistant", tool_calls: [{ name: "speak", arguments: { text: "先说结论" } }] },
    { role: "assistant", tool_calls: [{ name: "speak", arguments: { say_nothing: true, text: " x " } }] },
  ];
  const speaks = extractSpeaksFromTurn(turn);
  assert.equal(speaks.length, 2);
  assert.equal(speaks[0].text, "先说结论");
  assert.equal(speaks[1].sayNothing, true);
});

test("extractSpeaksFromTurn handles string arguments", () => {
  const stringArgsTurn = [
    { role: "assistant", tool_calls: [{ name: "speak", arguments: JSON.stringify({ text: "json" }) }] },
  ];
  assert.equal(extractSpeaksFromTurn(stringArgsTurn)[0].text, "json");
});

test("extractSpeaksFromTurn returns an empty list without a speak call", () => {
  const turn = [
    { role: "user", content: "first" },
    { role: "assistant", content: "纯文本", tool_calls: [] },
  ];
  assert.deepEqual(extractSpeaksFromTurn(turn), []);
});

test("front view keeps user rows, turns running rows into indicators, finished silent rows disappear", () => {
  const rows = [
    { id: "u1", role: "user", content: "把报表导出修一下" },
    { id: "a1", role: "assistant", status: "running", sections: [] },
  ];
  const front = displayRowsForView(rows, "front");
  assert.equal(front.length, 2);
  assert.equal(front[0].role, "user");
  assert.equal(front[1].frontWorking, true);

  const finishedSilent = [
    { id: "u1", role: "user", content: "任务" },
    { id: "a1", role: "assistant", status: "done", content: "内部草稿", sections: [] },
  ];
  const frontDone = displayRowsForView(finishedSilent, "front");
  assert.equal(frontDone.length, 1);
  assert.equal(frontDone[0].role, "user");
});

test("front view renders speak rows and drops intermediate work", () => {
  const rows = [
    { id: "u1", role: "user", content: "任务" },
    {
      id: "a1",
      role: "assistant",
      status: "done",
      sections: [{ type: "text", title: "Thinking", text: "内部思考" }],
      speaks: [
        { text: "进展汇报", attachments: [], sayNothing: false },
        { text: "给你的结论", attachments: [], sayNothing: false },
      ],
    },
    { id: "u2", role: "user", content: "收到" },
    {
      id: "a2",
      role: "assistant",
      status: "done",
      content: "",
      speaks: [{ text: null, attachments: [], sayNothing: true }],
    },
  ];
  const front = displayRowsForView(rows, "front");
  assert.equal(front.length, 4);
  assert.equal(front[1].frontSpeak, true);
  assert.deepEqual(front[1].speaks.map((speak) => speak.text), ["进展汇报", "给你的结论"]);
  assert.equal(front[3].speaks[0].sayNothing, true);
});

test("back view drops user inputs and keeps agent rows", () => {
  const rows = [
    { id: "u1", role: "user", content: "把报表导出修一下" },
    { id: "a1", role: "assistant", status: "done", sections: [{ type: "tool" }] },
    { id: "u2", role: "user", content: "收到" },
    { id: "a2", role: "assistant", status: "running", sections: [] },
  ];
  const back = displayRowsForView(rows, "back");
  assert.deepEqual(back.map((row) => row.id), ["a1", "a2"]);
  assert.equal(back[0].sections.length, 1);
});

test("off mode (realtime disabled) passes rows through untouched", () => {
  const rows = [
    { id: "u1", role: "user", content: "任务" },
    {
      id: "a1",
      role: "assistant",
      status: "done",
      content: "普通会话的回答",
      sections: [{ type: "tool" }],
    },
    { id: "internal", role: "user", kind: "lara.realtime.finalizer", content: "提醒" },
  ];
  assert.equal(displayRowsForView(rows, "off"), rows);
});

test("front filtering composes with speak history rows", () => {
  const rows = [
    { id: "u1", role: "user", content: "任务" },
    { id: "internal", role: "user", kind: "lara.realtime.finalizer", content: "提醒" },
    {
      id: "a1",
      role: "assistant",
      status: "done",
      speaks: [{ text: "说完了", attachments: ["out/result.txt"], sayNothing: false }],
    },
  ];
  const front = displayRowsForView(rows, "front");
  assert.deepEqual(front.map((row) => row.id), ["u1", "a1"]);
  assert.equal(front[1].speaks[0].attachments[0], "out/result.txt");
});

test("isLaraSaysSession matches only the dedicated session mode", () => {
  assert.equal(isLaraSaysSession({ mode: LARA_SAYS_SESSION_MODE }), true);
  assert.equal(isLaraSaysSession({ mode: "chat" }), false);
  assert.equal(isLaraSaysSession({}), false);
  assert.equal(isLaraSaysSession(null), false);
});
