import assert from "node:assert/strict";
import test, { after, before } from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { fileURLToPath } from "node:url";

// This spec sits one directory deeper than App.render.test.mjs, so the
// desktop root is four levels up.
const desktopRoot = fileURLToPath(new URL("../../../../", import.meta.url));let explorerModule;
let vite;

before(async () => {
  vite = await createServer({
    appType: "custom",
    logLevel: "silent",
    root: desktopRoot,
    server: { middlewareMode: true },
  });
  explorerModule = await vite.ssrLoadModule("/renderer/src/features/workspace/FileExplorer.jsx");
});

after(async () => {
  await vite?.close();
});

const UPLOAD_ENTRY = { name: "8efe-image.png", path: ".lara/uploads/8efe-image.png", kind: "file" };
const VIDEO_ENTRY = { name: "clip.webm", path: ".lara/uploads/clip.webm", kind: "file" };
const PLAIN_ENTRY = { name: "notes.txt", path: "notes.txt", kind: "file" };
const DIRECTORY_ENTRY = { name: ".lara", path: ".lara", kind: "directory" };

function renderEntry(entry) {
  return renderToStaticMarkup(React.createElement(explorerModule.FileTreeEntry, {
    api: {},
    entry,
    level: 1,
    scopeId: "project:E:/demo",
  }));
}

test("uploaded files get a scoped delete action in the file tree", () => {
  const html = renderEntry(UPLOAD_ENTRY);
  assert.match(html, /file-tree-row-delete/);
  assert.match(html, /aria-label="删除 \.lara\/uploads\/8efe-image\.png"/);
});

test("regular project files and directories never offer deletion", () => {
  assert.doesNotMatch(renderEntry(PLAIN_ENTRY), /file-tree-row-delete/);
  assert.doesNotMatch(renderEntry(DIRECTORY_ENTRY), /file-tree-row-delete/);
});

test("multimedia file rows show type icons instead of the text-file icon", () => {
  assert.match(renderEntry(UPLOAD_ENTRY), /lucide-image/);
  assert.match(renderEntry(VIDEO_ENTRY), /lucide-film/);
  assert.match(renderEntry(PLAIN_ENTRY), /lucide-file-text/);
});
