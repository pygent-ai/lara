import assert from "node:assert/strict";
import test from "node:test";
import { cleanProjectPath, isAbsoluteProjectPath, projectChoices, projectPathKey } from "../features/projects/projectPaths.js";
import { settingsPayload } from "../shared/api/client.js";

test("pasted Windows paths accept Explorer quotes, spaces, drives and network shares", () => {
  assert.equal(cleanProjectPath('  "C:\\My Projects\\lara"  '), "C:\\My Projects\\lara");
  for (const path of ['"C:\\My Projects\\lara"', "C:/", "\\\\server\\share", "/home/user/project"]) {
    assert.equal(isAbsoluteProjectPath(path), true, path);
  }
  for (const path of ["", "lara", "C:relative", "C:/one\nC:/two"]) {
    assert.equal(isAbsoluteProjectPath(path), false, path);
  }
});

test("recent projects deduplicate Windows casing and separators without merging POSIX case", () => {
  const projects = [
    { workspace_root: "c:/work/lara/", label: "lara" },
    { workspace_root: "D:\\work\\lara", label: "lara" },
    { workspace_root: "/work/Lara" }, { workspace_root: "/work/lara" },
  ];
  assert.equal(projectChoices(projects, "C:\\work\\lara").length, 4);
  assert.equal(projectChoices(projects, "C:\\work\\lara", "d:/work")[0].workspace_root, "D:\\work\\lara");
  assert.notEqual(projectPathKey("/work/Lara"), projectPathKey("/work/lara"));
});

test("switching projects leaves model routes and context settings untouched", () => {
  assert.deepEqual(settingsPayload({ workspaceRoot: "C:/project", agent: "" }), {
    workspace_root: "C:/project", agent_alias: "",
  });
  assert.deepEqual(settingsPayload({ contextWindow: "" }), { context_window: null });
});
