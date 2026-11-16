// Transcripts keyed per session, owned outside React state so a turn can keep
// streaming into a session the user switched away from.
//
// Each session holds up to two transcripts: the persisted history rendered
// from checkpoint rows, and the live one a running (or interrupted) turn
// owns on this client. The live transcript wins while it exists; the view
// reads whichever applies through subscribe/messages, so streaming writes
// land in exactly one place. The unbound stream of a pending new chat keys
// on the empty session id until pygent.v1.chat.started names its session.

const EMPTY_MESSAGES = Object.freeze([]);

export function createSessionTranscripts({ limit = 8 } = {}) {
  const entries = new Map();
  const listeners = new Set();

  const notify = () => {
    for (const listener of listeners) listener();
  };

  // LRU over sessions without a live transcript: a background turn must keep
  // its transcript however many sessions the user visits meanwhile, so live
  // entries neither count nor evict. A session dropped here simply reloads
  // its tail page on the next visit.
  const remember = (sessionId, entry) => {
    entries.delete(sessionId);
    entries.set(sessionId, entry);
    let idle = 0;
    for (const item of entries.values()) {
      if (!item.live) idle += 1;
    }
    for (const [key, item] of entries) {
      if (idle <= limit) break;
      if (!item.live) {
        entries.delete(key);
        idle -= 1;
      }
    }
  };

  const write = (sessionId, patch, { silent = false } = {}) => {
    const current = entries.get(sessionId) || {};
    const next = { ...current, ...patch };
    if (!next.live && !next.history) {
      entries.delete(sessionId);
    } else {
      remember(sessionId, next);
    }
    if (!silent) notify();
  };

  const messagesOf = (sessionId) => {
    const entry = entries.get(sessionId);
    return entry?.live || entry?.history || EMPTY_MESSAGES;
  };

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    notify,
    messages: messagesOf,
    live(sessionId) {
      return entries.get(sessionId)?.live;
    },
    hasLive(sessionId) {
      return Boolean(entries.get(sessionId)?.live);
    },
    setHistory(sessionId, messages) {
      write(sessionId, { history: messages });
    },
    setLive(sessionId, messages, options) {
      write(sessionId, { live: messages }, options);
    },
    // The updater runs once against the live transcript (seeded from the
    // visible history when a turn starts), so event projection is applied
    // exactly once even while the session is off screen.
    updateLive(sessionId, updater, options) {
      const current = entries.get(sessionId)?.live || messagesOf(sessionId);
      const next = updater(current);
      if (next !== current) write(sessionId, { live: next }, options);
      return next;
    },
    clearLive(sessionId) {
      if (!entries.get(sessionId)?.live) return;
      write(sessionId, { live: undefined });
    },
    // A finished turn's transcript stands in for persisted history until a
    // reload replaces it, so the view never flashes the pre-turn history.
    commitLive(sessionId) {
      const live = entries.get(sessionId)?.live;
      if (!live) return;
      write(sessionId, { live: undefined, history: live });
    },
    delete(sessionId) {
      if (entries.delete(sessionId)) notify();
    },
  };
}
