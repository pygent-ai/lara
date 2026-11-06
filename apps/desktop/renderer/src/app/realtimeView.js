// Realtime front/back dual-view state and row filtering (design §8).
//
// The front view (default) is the human conversation: user inputs, speak
// bubbles, the working indicator and fork replies. The back view is the
// workbench: the agent's content/thinking/tool stream without user inputs.
// Both views render from the same rows — the split is a pure filter, the
// stream and the journal stay untouched.
//
// Rows follow the App transcript model: {id, role: "user"|"assistant"|...,
// content, sections, status, speak?}. An assistant row carries `speak` when
// its turn delivered a speak call ({text, attachments, sayNothing}), and
// `frontWorking` marks a still-running turn in the front view.

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
  "lara.realtime.finalizer",
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

export function extractSpeakFromTurn(turnMessages) {
  const assistantMessages = (Array.isArray(turnMessages) ? turnMessages : [])
    .filter((message) => String(message?.role || "") === "assistant");
  for (let index = assistantMessages.length - 1; index >= 0; index -= 1) {
    const rawCalls = assistantMessages[index]?.tool_calls
      || assistantMessages[index]?.payload?.tool_calls
      || [];
    const calls = Array.isArray(rawCalls) ? rawCalls : [];
    for (let callIndex = calls.length - 1; callIndex >= 0; callIndex -= 1) {
      if (isSpeakToolCall(calls[callIndex])) {
        return speakArguments(calls[callIndex]);
      }
    }
  }
  return null;
}

/**
 * The view filter over transcript rows.
 *
 * Front: user rows stay, internal kinds drop, an assistant row becomes
 * either a speak bubble (row.speak), a working indicator (row.status
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
      if (row.speak) {
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
