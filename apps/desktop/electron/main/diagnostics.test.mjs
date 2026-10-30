import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test, { mock } from "node:test";

import { attachProcessDiagnostics, attachWindowDiagnostics } from "./diagnostics.mjs";

function createWindow({ reloads } = {}) {
  return {
    webContents: Object.assign(new EventEmitter(), {
      isDestroyed: () => false,
      reload: () => reloads?.push("reload"),
    }),
  };
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

test("an abnormally dead renderer is reloaded once to recover the window", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const reloads = [];
    const window = createWindow({ reloads });
    const logger = createLogger();
    attachWindowDiagnostics({ window, logger });

    window.webContents.emit("render-process-gone", {}, { reason: "oom", exitCode: -1 });
    assert.deepEqual(reloads, []);
    mock.timers.tick(1000);
    assert.deepEqual(reloads, ["reload"]);
  } finally {
    mock.timers.reset();
  }
});

test("a crash loop disables auto-reload instead of reloading forever", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const reloads = [];
    const window = createWindow({ reloads });
    const logger = createLogger();
    attachWindowDiagnostics({ window, logger });

    for (let attempt = 0; attempt < 3; attempt += 1) {
      window.webContents.emit("render-process-gone", {}, { reason: "crashed", exitCode: -1 });
      mock.timers.tick(1000);
    }
    assert.equal(reloads.length, 3);

    // Two further deaths inside the window must not schedule more reloads.
    window.webContents.emit("render-process-gone", {}, { reason: "crashed", exitCode: -1 });
    window.webContents.emit("render-process-gone", {}, { reason: "crashed", exitCode: -1 });
    mock.timers.tick(1000);
    assert.equal(reloads.length, 3);
    assert.ok(logger.errors.some((line) => line.includes("auto-reload disabled")));
  } finally {
    mock.timers.reset();
  }
});

test("a clean renderer exit schedules no reload", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const reloads = [];
    const window = createWindow({ reloads });
    attachWindowDiagnostics({ window, logger: createLogger() });

    window.webContents.emit("render-process-gone", {}, { reason: "clean-exit", exitCode: 0 });
    mock.timers.tick(1000);
    assert.deepEqual(reloads, []);
  } finally {
    mock.timers.reset();
  }
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
