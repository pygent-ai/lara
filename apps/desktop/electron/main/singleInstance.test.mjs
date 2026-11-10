import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { registerDevParentExit, registerSingleInstance } from "./singleInstance.mjs";

function createApp(acquired) {
  const app = new EventEmitter();
  app.quitCalls = 0;
  app.requestSingleInstanceLock = () => acquired;
  app.quit = () => {
    app.quitCalls += 1;
  };
  return app;
}

test("a second desktop instance exits before Chromium cache initialization", () => {
  const app = createApp(false);

  assert.equal(registerSingleInstance({ app, getWindow: () => null }), false);
  assert.equal(app.quitCalls, 1);
  assert.equal(app.listenerCount("second-instance"), 0);
});

test("the owning desktop instance restores and focuses its existing window", () => {
  const app = createApp(true);
  const calls = [];
  const window = {
    isMinimized: () => true,
    restore: () => calls.push("restore"),
    show: () => calls.push("show"),
    focus: () => calls.push("focus"),
  };

  assert.equal(registerSingleInstance({ app, getWindow: () => window }), true);
  app.emit("second-instance");

  assert.deepEqual(calls, ["restore", "show", "focus"]);
  assert.equal(app.quitCalls, 0);
});

test("a development instance exits when its launcher IPC channel disconnects", () => {
  const app = createApp(true);
  app.isPackaged = false;
  const processRef = new EventEmitter();
  processRef.connected = true;

  assert.equal(registerDevParentExit({ app, processRef }), true);
  processRef.emit("disconnect");

  assert.equal(app.quitCalls, 1);
});

test("packaged and independently launched instances do not watch for a dev parent", () => {
  const packagedApp = createApp(true);
  packagedApp.isPackaged = true;
  const packagedProcess = new EventEmitter();
  packagedProcess.connected = true;
  assert.equal(registerDevParentExit({ app: packagedApp, processRef: packagedProcess }), false);

  const standaloneApp = createApp(true);
  standaloneApp.isPackaged = false;
  const standaloneProcess = new EventEmitter();
  standaloneProcess.connected = false;
  assert.equal(registerDevParentExit({ app: standaloneApp, processRef: standaloneProcess }), false);

  packagedProcess.emit("disconnect");
  standaloneProcess.emit("disconnect");
  assert.equal(packagedApp.quitCalls, 0);
  assert.equal(standaloneApp.quitCalls, 0);
});
