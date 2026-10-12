import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { attachProcessDiagnostics, attachWindowDiagnostics } from "./diagnostics.mjs";

function createWindow() {
  return { webContents: new EventEmitter() };
}

function createLogger() {
  const errors = [];
  return { errors, error: (line) => errors.push(line) };
}

test("abnormal renderer death and unresponsiveness are logged with reason and exit code", () => {
  const window = createWindow();
  const logger = createLogger();
  attachWindowDiagnostics({ window, logger });

  window.webContents.emit("render-process-gone", {}, { reason: "oom", exitCode: -1 });
  window.webContents.emit("unresponsive");

  assert.deepEqual(logger.errors, [
    "[lara] renderer gone: reason=oom exitCode=-1",
    "[lara] renderer unresponsive",
  ]);
});

test("a clean renderer exit from a normal window close is not logged", () => {
  const window = createWindow();
  const logger = createLogger();
  attachWindowDiagnostics({ window, logger });

  window.webContents.emit("render-process-gone", {}, { reason: "clean-exit", exitCode: 0 });

  assert.deepEqual(logger.errors, []);
});

test("renderer console errors are forwarded in development, other levels are not", () => {
  const window = createWindow();
  const logger = createLogger();
  attachWindowDiagnostics({ window, logger, isPackaged: false });

  window.webContents.emit("console-message", {
    level: "error",
    message: "boom",
    sourceId: "http://127.0.0.1:5173/renderer/src/app/App.jsx",
    lineNumber: 42,
  });
  window.webContents.emit("console-message", {
    level: "warning",
    message: "meh",
    sourceId: "http://127.0.0.1:5173/app.js",
    lineNumber: 1,
  });

  assert.deepEqual(logger.errors, [
    "[lara] renderer error: boom (http://127.0.0.1:5173/renderer/src/app/App.jsx:42)",
  ]);
});

test("packaged builds do not forward renderer console output", () => {
  const window = createWindow();
  const logger = createLogger();
  attachWindowDiagnostics({ window, logger, isPackaged: true });

  window.webContents.emit("console-message", {
    level: "error",
    message: "boom",
    sourceId: "app.js",
    lineNumber: 1,
  });

  assert.deepEqual(logger.errors, []);
});

test("main frame load failures are logged, aborted navigations and subframes are not", () => {
  const window = createWindow();
  const logger = createLogger();
  attachWindowDiagnostics({ window, logger });

  window.webContents.emit("did-fail-load", {}, -3, "ERR_ABORTED", "http://127.0.0.1:5173/", true);
  window.webContents.emit("did-fail-load", {}, -2, "ERR_FAILED", "http://127.0.0.1:5173/", false);
  assert.deepEqual(logger.errors, []);

  window.webContents.emit("did-fail-load", {}, -2, "ERR_FAILED", "http://127.0.0.1:5173/", true);
  assert.deepEqual(logger.errors, [
    "[lara] main frame failed to load: ERR_FAILED (-2) http://127.0.0.1:5173/",
  ]);
});

test("non-renderer child process deaths are logged with type, reason and exit code", () => {
  const app = new EventEmitter();
  const logger = createLogger();
  attachProcessDiagnostics({ app, logger });

  app.emit("child-process-gone", {}, { type: "GPU", reason: "crashed", exitCode: 805306368, name: "GPU" });

  assert.deepEqual(logger.errors, [
    "[lara] GPU process gone: reason=crashed exitCode=805306368 name=GPU",
  ]);
});
