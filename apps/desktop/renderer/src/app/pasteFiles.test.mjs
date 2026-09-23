import assert from "node:assert/strict";
import { test } from "node:test";

import { dataTransferHasFiles, filesFromDataTransfer } from "./pasteFiles.js";

function fileItem(name) {
  return { kind: "file", getAsFile: () => ({ name }) };
}

const stringItem = { kind: "string", getAsFile: () => null };

test("filesFromDataTransfer collects only file items", () => {
  const dataTransfer = {
    items: [fileItem("a.png"), stringItem, fileItem("notes.txt")],
  };
  const files = filesFromDataTransfer(dataTransfer);
  assert.deepEqual(files.map((file) => file.name), ["a.png", "notes.txt"]);
});

test("filesFromDataTransfer tolerates missing or empty transfers", () => {
  assert.deepEqual(filesFromDataTransfer(undefined), []);
  assert.deepEqual(filesFromDataTransfer({ items: [] }), []);
  assert.deepEqual(filesFromDataTransfer({ items: [stringItem] }), []);
});

test("filesFromDataTransfer skips items whose blob is unavailable", () => {
  const dataTransfer = {
    items: [{ kind: "file", getAsFile: () => null }, fileItem("b.png")],
  };
  assert.deepEqual(filesFromDataTransfer(dataTransfer).map((f) => f.name), ["b.png"]);
});

test("dataTransferHasFiles checks the Files type entry", () => {
  assert.equal(dataTransferHasFiles({ types: ["Files", "text/plain"] }), true);
  assert.equal(dataTransferHasFiles({ types: ["text/plain"] }), false);
  assert.equal(dataTransferHasFiles(undefined), false);
});
