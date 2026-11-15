# Inspector Feature

State and data flow for the right-hand execution inspector (the panel itself
still renders from `app/App.jsx` as `TracePanel`).

The inspector has two data sources with different jobs:

- **Session activity feed** (`activityFeed.js`, `useActivityFeed.js`): the
  source of truth for the 工具 / 文件活动 tabs and the overview counts. It
  mirrors the backend's append-only session logs through
  `GET /traces/{session}/activity`. The first page is the latest window; every
  later page passes the returned `cursor` back as `after` and receives only
  the rows appended since, so the hook can poll every 2 s while the turn runs
  (an idle page is a few bytes) and settle once more after it stops. Totals
  (`tool_calls_total`, `file_events_total`) always cover the whole session,
  so counts stay right when only a window of rows is loaded.
- **Live execution stream**: overlays the calls still in flight (requested but
  not yet audited into the logs) and feeds the 事件 tab. `eventKinds.js`
  classifies the SDK journal: streaming deltas, span boundaries, effect
  bookkeeping and per-attempt accounting are telemetry and never enter the
  bounded live buffer, so the readable events are not evicted by them.

The cap on the live buffer therefore only bounds the raw event list; the tool
and file history is never reconstructed from a capped buffer.
