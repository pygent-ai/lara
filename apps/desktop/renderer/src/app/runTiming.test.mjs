import assert from "node:assert/strict";
import test from "node:test";
import { activityHeaderText, runTimingFields } from "./runTiming.js";

test("durable elapsed time stays fixed after completion and reopening", () => {
  const message = runTimingFields({
    case_run_id: "run-1", status: "passed",
    started_at: "2026-09-04T10:00:00+00:00",
    finished_at: "2026-09-04T10:01:15+00:00",
  });
  assert.equal(activityHeaderText(message, Date.now()), "已完成 1 分 15 秒");
  assert.equal(activityHeaderText(message, Date.now() + 86400000), "已完成 1 分 15 秒");
});

test("missing or invalid history timing never fabricates zero seconds", () => {
  for (const timing of [undefined, {}, { started_at: "bad", finished_at: null }, {
    started_at: "2026-09-04T10:00:01Z", finished_at: "2026-09-04T10:00:00Z",
  }]) {
    assert.equal(activityHeaderText(runTimingFields(timing), 1000), "已完成");
  }
});

test("live time advances and failed or subsecond runs freeze correctly", () => {
  assert.equal(activityHeaderText({ status: "running", startedAt: 1000 }, 4000), "处理中 3 秒");
  assert.equal(activityHeaderText({ status: "error", startedAt: 1000, endedAt: 4500 }, 9000), "3 秒后失败");
  assert.equal(activityHeaderText({ status: "success", startedAt: 1000, endedAt: 1200 }, 9000), "已完成 0 秒");
});
