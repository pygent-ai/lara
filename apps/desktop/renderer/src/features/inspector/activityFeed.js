// The session activity feed is the inspector's source of truth for tool
// calls and file activity. It mirrors the backend's append-only session logs:
// the first page is the latest window, every later page carries only the rows
// appended since the cursor the previous page returned. Live stream events
// are layered on top by the panel for in-flight state only; they never need
// to survive in a bounded buffer for the history to stay complete.

// Bounds the rows kept after many incremental pages; the oldest rows go
// first and the feed reports itself truncated, like a windowed initial page.
export const ACTIVITY_FEED_MAX_EVENTS = 3000;

export const EMPTY_ACTIVITY_FEED = Object.freeze({
  events: [],
  cursor: "",
  shown: 0,
  total: 0,
  truncated: false,
  toolCallsTotal: 0,
  fileEventsTotal: 0,
});

// Fold one API page into the feed. The initial page (no cursor was sent)
// replaces the feed; an incremental page appends rows the feed does not hold
// yet. Dedupe is by event id because a retried poll can re-deliver a page.
export function applyActivityPage(feed, response, { incremental = false, maxEvents = ACTIVITY_FEED_MAX_EVENTS } = {}) {
  const page = Array.isArray(response?.events) ? response.events : [];
  const totals = {
    total: Number(response?.events_total) || 0,
    toolCallsTotal: Number(response?.tool_calls_total) || 0,
    fileEventsTotal: Number(response?.file_events_total) || 0,
  };
  if (!incremental) {
    return {
      events: page,
      cursor: String(response?.cursor || ""),
      shown: page.length,
      truncated: Boolean(response?.events_truncated),
      ...totals,
    };
  }
  const known = new Set(feed.events.map((event) => event.id));
  const fresh = page.filter((event) => !event?.id || !known.has(event.id));
  if (!fresh.length && String(response?.cursor || "") === feed.cursor && totals.total === feed.total) {
    return feed;
  }
  let events = fresh.length ? [...feed.events, ...fresh] : feed.events;
  let truncated = feed.truncated;
  if (events.length > maxEvents) {
    events = events.slice(-maxEvents);
    truncated = true;
  }
  return {
    events,
    cursor: String(response?.cursor || feed.cursor),
    shown: events.length,
    truncated,
    ...totals,
  };
}
