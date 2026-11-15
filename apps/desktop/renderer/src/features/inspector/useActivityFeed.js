import { useCallback, useEffect, useRef, useState } from "react";

import { TRACE_EVENT_LIMIT } from "../../app/activeSessionSync.js";
import { EMPTY_ACTIVITY_FEED, applyActivityPage } from "./activityFeed.js";

export const ACTIVITY_POLL_MS = 2000;
// The last tool audit rows can trail the terminal event slightly; one delayed
// fetch after the turn ends absorbs them.
export const ACTIVITY_SETTLE_MS = 2500;

// One page fetch folded into the feed: an incremental page when the feed has
// a cursor, else the initial window. A cursor the backend can no longer
// satisfy (400) falls back to the initial window; an unknown session (404)
// yields an empty feed.
export async function fetchActivityPage(api, sessionId, feed, { eventLimit = TRACE_EVENT_LIMIT, signal } = {}) {
  const after = feed.cursor || undefined;
  try {
    const response = await api.getSessionActivity(sessionId, { eventLimit, after, signal });
    return applyActivityPage(feed, response, { incremental: Boolean(after) });
  } catch (err) {
    if (err?.status === 404) {
      return EMPTY_ACTIVITY_FEED;
    }
    if (err?.status === 400 && after) {
      const response = await api.getSessionActivity(sessionId, { eventLimit, signal });
      return applyActivityPage(feed, response, { incremental: false });
    }
    throw err;
  }
}

// Follows the activity feed of the open session: the initial window on a
// session switch, incremental pages while the session runs, and one settling
// fetch after it stops. Failed polls keep the last state; the next tick
// retries.
export function useActivityFeed({ api, sessionId, running, pollMs = ACTIVITY_POLL_MS, eventLimit = TRACE_EVENT_LIMIT }) {
  const [feed, setFeed] = useState(EMPTY_ACTIVITY_FEED);
  const feedRef = useRef(EMPTY_ACTIVITY_FEED);
  const sessionRef = useRef("");
  const inflightRef = useRef("");
  const wasRunningRef = useRef(false);

  const load = useCallback(async (targetSessionId) => {
    // One request per session at a time; a switch mid-flight starts the new
    // session's load regardless and the stale result is dropped below.
    if (!targetSessionId || inflightRef.current === targetSessionId) return;
    inflightRef.current = targetSessionId;
    try {
      const current = sessionRef.current === targetSessionId ? feedRef.current : EMPTY_ACTIVITY_FEED;
      const next = await fetchActivityPage(api, targetSessionId, current, { eventLimit });
      if (sessionRef.current !== targetSessionId) return;
      if (next !== feedRef.current) {
        feedRef.current = next;
        setFeed(next);
      }
    } catch {
      // Additive UI: a failed poll keeps the last state and the next tick retries.
    } finally {
      if (inflightRef.current === targetSessionId) inflightRef.current = "";
    }
  }, [api, eventLimit]);

  useEffect(() => {
    sessionRef.current = sessionId;
    feedRef.current = EMPTY_ACTIVITY_FEED;
    setFeed(EMPTY_ACTIVITY_FEED);
    if (sessionId) void load(sessionId);
  }, [sessionId, load]);

  useEffect(() => {
    if (!sessionId || !running) return undefined;
    const timer = window.setInterval(() => { void load(sessionId); }, pollMs);
    return () => window.clearInterval(timer);
  }, [sessionId, running, pollMs, load]);

  useEffect(() => {
    const wasRunning = wasRunningRef.current;
    wasRunningRef.current = running;
    if (!sessionId || running || !wasRunning) return undefined;
    const timer = window.setTimeout(() => { void load(sessionId); }, ACTIVITY_SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [sessionId, running, load]);

  return feed;
}
