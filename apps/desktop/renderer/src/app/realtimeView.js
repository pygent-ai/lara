// Realtime front/back dual-view state and row filtering (design §8).
//
// The front view (default) is the human conversation: user inputs, speak
// bubbles, the working indicator and fork replies. The back view is the
// workbench: the agent's content/thinking/tool stream without user inputs.
// Both views render from the same rows — the split is a pure filter, the
// stream and the journal stay untouched.
//
// Rows follow the App transcript model: {id, role: "user"|"assistant"|...,
// content, sections, status, speaks?}. An assistant row carries `speaks` —
// every speak call its turn delivered, in order ({text, attachments,
// sayNothing}) — because a silent-work turn reports progress and its final
// answer through several speak calls; `frontWorking` marks a still-running
// turn in the front view.

export const VIEW_MODES = ["front", "back"];
const STORAGE_KEY = "lara.desktop.realtime-view-modes.v1";

// The dedicated sidebar session ("Lara说") is the only one where realtime
// interaction exists; the backend gates the feature on this session mode.
export const LARA_SAYS_SESSION_MODE = "lara-says";

export function isLaraSaysSession(session) {
  return String(session?.mode || "") === LARA_SAYS_SESSION_MODE;
}

// Kinds that are internal coordination signals: never rendered as rows in
// either view (envelope/nudge ride inside tool results and are naturally
// invisible in the front view).
const INTERNAL_KINDS = new Set([
  "pygent.v1.realtime.finalizer",
]);

export function normalizeViewMode(mode) {
  return mode === "back" ? "back" : "front";
}

export function loadViewMode(sessionId) {
  if (!sessionId) return "front";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const modes = raw ? JSON.parse(raw) : {};
    return normalizeViewMode(modes?.[sessionId]);
  } catch {
    return "front";
  }
}

export function storeViewMode(sessionId, mode) {
  if (!sessionId) return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const modes = raw ? JSON.parse(raw) : {};
    modes[sessionId] = normalizeViewMode(mode);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(modes));
  } catch {
    // Persistence is best-effort; the in-memory mode still applies.
  }
}

export function isInternalMessage(message) {
  return INTERNAL_KINDS.has(String(message?.kind || ""));
}

export function isSpeakToolCall(call) {
  return String(call?.name || "") === "speak" || String(call?.function?.name || "") === "speak";
}

export function speakArguments(raw) {
  const value = raw?.arguments ?? raw?.args ?? raw?.function?.arguments;
  let parsed = value;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return null;
    }
  }
  if (!parsed || typeof parsed !== "object") return null;
  const sayNothing = parsed.say_nothing === true;
  const text = sayNothing ? null : String(parsed.text || "").trim() || null;
  const attachments = Array.isArray(parsed.attachments)
    ? parsed.attachments.map((item) => String(item))
    : [];
  if (!sayNothing && !text && attachments.length === 0) return null;
  return { text, attachments, sayNothing };
}

export function extractSpeaksFromTurn(turnMessages) {
  // Collect every speak call the turn delivered, in order: a silent-work
  // turn reports progress and its final answer through several speak calls,
  // and rendering only the last one would erase the earlier replies.
  const assistantMessages = (Array.isArray(turnMessages) ? turnMessages : [])
    .filter((message) => String(message?.role || "") === "assistant");
  const speaks = [];
  for (const message of assistantMessages) {
    const rawCalls = message?.tool_calls
      || message?.payload?.tool_calls
      || [];
    const calls = Array.isArray(rawCalls) ? rawCalls : [];
    for (const call of calls) {
      if (!isSpeakToolCall(call)) continue;
      const args = speakArguments(call);
      if (args) speaks.push(args);
    }
  }
  return speaks;
}

/**
 * The view filter over transcript rows.
 *
 * Front: user rows stay, internal kinds drop, an assistant row becomes
 * either speak bubbles (row.speaks), a working indicator (row.status
 * "running"), or disappears (finished without speaking — invisible work).
 * Back: user inputs and internal kinds drop; assistant rows pass as-is.
 */
export function displayRowsForView(items, mode) {
  // Sessions without realtime interaction render exactly as before: "off"
  // bypasses before normalization (normalizeViewMode would fold it to front).
  if (mode === "off") return Array.isArray(items) ? items : [];
  const view = normalizeViewMode(mode);
  const rows = Array.isArray(items) ? items : [];
  if (view === "back") {
    return rows.filter((row) => !isInternalMessage(row) && row?.role !== "user");
  }
  const output = [];
  for (const row of rows) {
    if (isInternalMessage(row)) continue;
    if (row?.role === "assistant") {
      if (row.status === "running") {
        output.push({ ...row, frontWorking: true });
        continue;
      }
      if (Array.isArray(row.speaks) && row.speaks.length > 0) {
        output.push({ ...row, frontSpeak: true });
      }
      continue;
    }
    if (row?.role === "user" || row?.role === "resume" || row?.role === "automation") {
      output.push(row);
    }
  }
  return output;
}
