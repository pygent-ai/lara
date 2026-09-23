export const ACKNOWLEDGED_SESSION_STATUSES_KEY = "lara.desktop.acknowledged-session-statuses.v1";

export function sessionStatusIdentity(session) {
  return [
    session?.last_case_run_status || sessionStatusKind(session?.last_case_run_status),
    session?.updated_at || session?.created_at || "",
  ].join(":");
}

export function loadStoredSessionStatuses(key, storage = browserStorage()) {
  if (!storage) {
    return {};
  }

  try {
    const parsed = JSON.parse(storage.getItem(key) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([sessionId, identity]) => typeof sessionId === "string" && typeof identity === "string",
      ),
    );
  } catch {
    return {};
  }
}

export function hasStoredSessionStatuses(key, storage = browserStorage()) {
  if (!storage) {
    return false;
  }
  try {
    return storage.getItem(key) !== null;
  } catch {
    return false;
  }
}

export function storeSessionStatuses(key, statuses, storage = browserStorage()) {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(key, JSON.stringify(statuses));
  } catch {
    // Read markers are optional UI state; storage failures must not block navigation.
  }
}

export function loadAcknowledgedSessionStatuses(storage = browserStorage()) {
  return loadStoredSessionStatuses(ACKNOWLEDGED_SESSION_STATUSES_KEY, storage);
}

export function acknowledgeStoredSessionStatus(current, session, storage = browserStorage()) {
  const sessionId = String(session?.session_id || "");
  if (!sessionId || sessionStatusKind(session?.last_case_run_status) === "running") {
    return current;
  }

  const next = {
    ...current,
    [sessionId]: sessionStatusIdentity(session),
  };
  persistAcknowledgedSessionStatuses(next, storage);
  return next;
}

export function persistAcknowledgedSessionStatuses(statuses, storage = browserStorage()) {
  storeSessionStatuses(ACKNOWLEDGED_SESSION_STATUSES_KEY, statuses, storage);
}

function browserStorage() {
  try {
    return globalThis.window?.localStorage || null;
  } catch {
    return null;
  }
}

export function sessionStatusKind(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("run")) {
    return "running";
  }
  if (value.includes("error") || value.includes("fail")) {
    return "error";
  }
  return "success";
}
