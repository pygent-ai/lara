import assert from "node:assert/strict";
import test from "node:test";
import { changeTypeBadge, summarizeRunTurns, summarizeTurns, turnChangesForMessage } from "./fileChangesState.js";

const TURNS = [
  {
    case_run_id: "run-1", turn_id: "turn-1", change_count: 3,
    total_additions: 9, total_deletions: 3,
    files: [
      { path: "src/a.py", change_type: "modified", additions: 5, deletions: 2, changes: 2, diff_id: "diff_a", patch_available: true },
      { path: "src/b.py", change_type: "created", additions: 4, deletions: 1, changes: 1, diff_id: "diff_b", patch_available: true },
    ],
  },
  {
    case_run_id: "run-2", turn_id: "turn-2", change_count: 1,
    total_additions: null, total_deletions: 0,
    files: [
      { path: "src/a.py", change_type: "deleted", additions: null, deletions: 0, changes: 1, diff_id: "diff_c", patch_available: false },
    ],
  },
];

test("a bubble matches its own turns and falls back to the whole run", () => {
  const bubble = { caseRunId: "run-1", turnIds: ["turn-1"] };
  assert.deepEqual(turnChangesForMessage(TURNS, bubble), [TURNS[0]]);
  // Live bubbles carry the run id before the history reload attaches turn ids.
  const liveBubble = { caseRunId: "run-1" };
  assert.equal(turnChangesForMessage(TURNS, liveBubble).length, 1);
  assert.equal(turnChangesForMessage(TURNS, { caseRunId: "run-9" }).length, 0);
  assert.equal(turnChangesForMessage(TURNS, {}).length, 0);
});

test("summaries fold files across turns and keep unknown counts null", () => {
  const summary = summarizeTurns(TURNS);
  assert.equal(summary.fileCount, 2);
  assert.equal(summary.changeCount, 4);
  // src/a.py appears in both turns: counts accumulate, the later turn's
  // label and diff link win.
  const merged = summary.files.find((file) => file.path === "src/a.py");
  assert.equal(merged.changeType, "deleted");
  assert.equal(merged.diffId, "diff_c");
  assert.equal(merged.changes, 3);
  assert.equal(merged.additions, null);
  assert.equal(summary.deletions, 3);
  assert.equal(summary.additions, null);
});

test("the live strip folds every turn of the running case run", () => {
  const summary = summarizeRunTurns(TURNS, "run-1");
  assert.equal(summary.fileCount, 2);
  assert.equal(summary.changeCount, 3);
  assert.equal(summarizeRunTurns(TURNS, ""), null);
  assert.equal(summarizeRunTurns(TURNS, "run-9"), null);
});

test("badges map change types to the git shorthand", () => {
  assert.equal(changeTypeBadge("created"), "A");
  assert.equal(changeTypeBadge("modified"), "M");
  assert.equal(changeTypeBadge("deleted"), "D");
  assert.equal(changeTypeBadge("unknown"), "M");
});
