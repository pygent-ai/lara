// React 19's development build instruments every render with
// performance.measure(name, { detail: { ...props diff } }) — and Chromium
// retains user-timing entries without any buffer cap (verified on Electron 39:
// 200k measured entries, all retained). A long streaming turn re-renders at
// frame cadence, so a dev session accumulates millions of retained entries
// until the renderer dies with "Failed to execute 'measure' on 'Performance':
// Data cannot be cloned, out of memory", followed by a crash.
//
// Shadowing measure before react-dom is first evaluated flips its
// supportsUserTiming gate (typeof performance.measure === "function") to
// false, so the dev build emits nothing at all. Nothing else in the renderer
// calls performance.measure. Remove this module from main.jsx to profile
// React renders in the Performance panel.
if (import.meta.env?.DEV) {
  globalThis.performance.measure = undefined;
}
