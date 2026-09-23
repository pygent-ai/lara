import assert from "node:assert/strict";
import test from "node:test";

import { applyBadgeOverlay } from "./badge.mjs";

test("a positive count sets the overlay icon with an accessible label", () => {
  const window = createWindow();
  const icon = { isEmpty: () => false };

  const applied = applyBadgeOverlay({
    window,
    count: 3,
    dataUrl: "data:image/png;base64,abc",
    createImage: () => icon,
    platform: "win32",
  });

  assert.equal(applied, true);
  assert.deepEqual(window.calls, [{ icon, description: "有 3 个会话已完成，待查看" }]);
});

test("a zero or negative count clears the overlay", () => {
  const window = createWindow();

  assert.equal(
    applyBadgeOverlay({ window, count: 0, dataUrl: null, createImage: () => null, platform: "win32" }),
    true,
  );
  assert.deepEqual(window.calls, [{ icon: null, description: "" }]);

  assert.equal(
    applyBadgeOverlay({ window, count: -4, dataUrl: null, createImage: () => null, platform: "win32" }),
    true,
  );
  assert.equal(window.calls.length, 2);
});

test("payloads outside the renderer png contract are rejected", () => {
  const window = createWindow();

  assert.equal(
    applyBadgeOverlay({ window, count: 2, dataUrl: "data:text/html;base64,abc", createImage: () => ({}), platform: "win32" }),
    false,
  );
  assert.equal(
    applyBadgeOverlay({ window, count: 2, dataUrl: undefined, createImage: () => null, platform: "win32" }),
    false,
  );
  assert.equal(
    applyBadgeOverlay({ window, count: 2, dataUrl: "data:image/png;base64,abc", createImage: () => ({ isEmpty: () => true }), platform: "win32" }),
    false,
  );
  assert.deepEqual(window.calls, []);
});

test("other platforms and unusable windows are ignored", () => {
  assert.equal(
    applyBadgeOverlay({ window: createWindow(), count: 1, dataUrl: "data:image/png;base64,abc", createImage: () => ({ isEmpty: () => false }), platform: "darwin" }),
    false,
  );

  const destroyed = createWindow();
  destroyed.isDestroyed = () => true;
  assert.equal(
    applyBadgeOverlay({ window: destroyed, count: 1, dataUrl: "data:image/png;base64,abc", createImage: () => ({ isEmpty: () => false }), platform: "win32" }),
    false,
  );
  assert.equal(
    applyBadgeOverlay({ window: null, count: 1, dataUrl: "data:image/png;base64,abc", createImage: () => ({ isEmpty: () => false }), platform: "win32" }),
    false,
  );
});

function createWindow() {
  const calls = [];
  return {
    calls,
    isDestroyed: () => false,
    setOverlayIcon: (icon, description) => calls.push({ icon, description }),
  };
}
