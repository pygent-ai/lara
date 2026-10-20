import { SettingsPage as SettingsPanel } from "../features/settings/SettingsPage.jsx";
import { settingsForSave } from "../features/settings/settingsModel.js";
export { SettingsPanel };
export { modelGroupValidationError, settingsForSave, protocolLabel, providerSelectionValue, renamedConnectionValue, protocolChoicesForConnection, capabilityPresetLabel } from "../features/settings/settingsModel.js";
import React, { lazy, memo, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  Folder,
  FolderCode,
  FolderPlus,
  GitFork,
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Check,
  CalendarClock,
  Copy,
  Download,
  Film,
  FileText,
  Image as ImageIcon,
  Music,
  Paperclip,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pause,
  Pin,
  Play,
  Plus,
  Search,
  Settings2,
  Square,
  Trash2,
  X,
} from "lucide-react";
import laraLogo from "../assets/lara-logo.png";

import { createApiClient } from "../shared/api/client.js";
import { attachmentDisplayName, attachmentFileName, attachmentMediaType, isDocumentAttachment, isUploadedAttachment } from "../shared/media/mediaTypes.js";
import { createSessionGroupSync } from "./sessionGroupSync.js";
import {
  CONTEXT_SNAPSHOT_LIMIT,
  SESSION_HISTORY_LIMIT,
  TRACE_EVENT_LIMIT,
  refreshActiveSession,
} from "./activeSessionSync.js";
import { loadWorkbenchPreferences, saveWorkbenchPreferences } from "./workbenchPreferences.js";
import { DEFAULT_PANEL_WIDTHS, PANEL_LIMITS, normalizePanelWidths, fitPanelWidths } from "./panelWidths.js";
import { projectPathKey } from "../features/projects/projectPaths.js";
import { activityHeaderText, runTimingFields } from "./runTiming.js";
import { dataTransferHasFiles, filesFromDataTransfer } from "./pasteFiles.js";
import {
  adaptLayoutToCompactViewport,
  COMPACT_LAYOUT_QUERY,
  createInitialLayoutState,
  toggleHistory,
  toggleTrace,
} from "./layoutState.js";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import { createPortal } from "react-dom";
import remarkGfmNoAutolink from "./remarkGfmNoAutolink.js";
import {
  acknowledgeStoredSessionStatus,
  loadAcknowledgedSessionStatuses,
  sessionStatusIdentity,
} from "./sessionStatusState.js";
import {
  countUnacknowledgedCompletedSessions,
  isUnacknowledgedCompletedSession,
  loadBadgeBaseline,
  seedBadgeBaseline,
  updateTaskbarBadge,
} from "./taskbarBadge.js";

function deferredPanel(load, exportName) {
  const Component = lazy(() => load().then((module) => ({ default: module[exportName] })));
  return function DeferredPanel(props) {
    return <Suspense fallback={<div className="empty-state" role="status">正在加载…</div>}>
      <Component {...props} />
    </Suspense>;
  };
}

const FileExplorer = deferredPanel(() => import("../features/workspace/FileExplorer.jsx"), "FileExplorer");
const PowerShellPanel = deferredPanel(() => import("../features/workspace/PowerShellPanel.jsx"), "PowerShellPanel");
const ScheduledPage = deferredPanel(() => import("../features/automations/ScheduledPage.jsx"), "ScheduledPage");

const EMPTY_SETTINGS = {
  workspace_root: "",
  lara_root: "",
  agent: "default",
  model_configuration_status: "unconfigured",
  model_configuration_error: "model_configuration_required",
  connections: {},
  models: {},
  model_groups: {},
  default_model_group: "",
  retry: null,
  user_lara_root: "",
  max_steps: -1,
  context_window: null,
  context_compression_trigger_ratio: 0.9,
};

const TRACE_TABS = ["Overview", "Tools", "Changes", "Events", "Context", "Config"];
const INSPECTOR_LABELS = { Overview: "概览", Events: "事件", Context: "上下文", Tools: "工具", Changes: "文件活动", Config: "配置", Trace: "执行详情", Files: "文件", PowerShell: "终端" };
const TOOL_ARGUMENT_PREVIEW_LIMIT = 4_000;
const TOOL_RESULT_PREVIEW_LIMIT = 6_000;
const TRACE_RENDER_LIMIT = 300;
const INITIAL_TRANSCRIPT_MESSAGE_LIMIT = 60;
// Re-attach replay catch-up: an event recorded within this window counts as
// live, and while catching up the view paints at most once per flush window.
const REPLAY_LIVE_THRESHOLD_MS = 15000;
const REPLAY_VIEW_FLUSH_MS = 1000;
const EMPTY_SESSION_ACTIVITY = { events: [], shown: 0, total: 0, truncated: false };
const EMPTY_TRACE_WINDOW = { shown: 0, total: 0, truncated: false };

export function App() {
  const api = useMemo(() => createApiClient(), []);
  const workbenchRef = useRef(null);
  const [panelWidths, setPanelWidths] = useState(() => normalizePanelWidths(loadWorkbenchPreferences().panelWidths));
  const [workbenchWidth, setWorkbenchWidth] = useState(globalThis.window?.innerWidth || 1360);
  const [resizingPanel, setResizingPanel] = useState(false);
  useEffect(() => {
    const node = workbenchRef.current;
    const observer = new ResizeObserver(([entry]) => setWorkbenchWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!resizingPanel) saveWorkbenchPreferences({ panelWidths });
  }, [panelWidths, resizingPanel]);
  const [layout, setLayout] = useState(() => {
    const compact = compactLayoutMatches();
    const saved = loadWorkbenchPreferences().layout;
    const initial = createInitialLayoutState({ compact });
    return { ...initial, ...(!compact && saved && typeof saved.historyCollapsed === "boolean" && typeof saved.traceCollapsed === "boolean" ? saved : {}), compact };
  });
  const [settings, setSettings] = useState(EMPTY_SETTINGS);
  const [pendingNewModelGroup, setPendingNewModelGroup] = useState("");
  const [pendingNewSessionScope, setPendingNewSessionScope] = useState("");
  useEffect(() => {
    setPendingNewModelGroup((current) => current && settings.model_groups?.[current]
      ? current
      : settings.default_model_group || "");
  }, [settings.default_model_group, settings.model_groups]);
  useEffect(() => {
    if (!layout.compact) saveWorkbenchPreferences({ layout: { historyCollapsed: layout.historyCollapsed, traceCollapsed: layout.traceCollapsed } });
  }, [layout]);
  const [projects, setProjects] = useState([]);
  const [sessionGroups, setSessionGroups] = useState([]);
  const sessionGroupSync = useMemo(() => createSessionGroupSync(api, (groups) => {
    setSessionGroups((current) => JSON.stringify(current) === JSON.stringify(groups) ? current : groups);
  }), [api]);
  useEffect(() => sessionGroupSync.start({ window, document }), [sessionGroupSync]);
  const [activeScopeId, setActiveScopeId] = useState("");
  const [activeSession, setActiveSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [activityCollapseToken, setActivityCollapseToken] = useState(0);
  const [pendingSteerings, setPendingSteerings] = useState({});
  const [traceEvents, setTraceEvents] = useState([]);
  const [sessionActivity, setSessionActivity] = useState(EMPTY_SESSION_ACTIVITY);
  const [traceWindow, setTraceWindow] = useState(EMPTY_TRACE_WINDOW);
  const [liveEvents, setLiveEvents] = useState([]);
  const [contextSnapshots, setContextSnapshots] = useState([]);
  const [status, setStatus] = useState("Loading");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [runningSessionIds, setRunningSessionIds] = useState({});
  const [steeringSessionIds, setSteeringSessionIds] = useState({});
  const [approvals, setApprovals] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const settingsSaveBusyRef = useRef(false);
  const [activeView, setActiveView] = useState("chat");
  const [automations, setAutomations] = useState([]);
  const projectChooserBusyRef = useRef(false);
  const activeSessionIdRef = useRef("");
  const messagesRef = useRef([]);
  const runningSessionIdsRef = useRef({});
  const pendingSessionMessagesRef = useRef(new Map());
  const sessionViewCacheRef = useRef(new Map());
  // Composer text keyed per session (or per scope for a pending new chat). A
  // ref keeps keystrokes from re-rendering the whole App, while ChatPane —
  // which remounts on every session switch — re-reads it on mount.
  const composerDraftsRef = useRef(new Map());
  const sessionLiveEventsRef = useRef(new Map());
  const activeExecutionsRef = useRef(new Map());
  const sessionLoadTokenRef = useRef(0);
  const resumeSessionRef = useRef(null);
  const traceLoadTokenRef = useRef(0);
  const activeSessionSignatureRef = useRef("");
  const traceEventsSignatureRef = useRef("");
  const activitySignatureRef = useRef("");
  const contextSnapshotsSignatureRef = useRef("");
  const messagesHistorySignatureRef = useRef("");
  const traceCollapsedRef = useRef(layout.traceCollapsed);
  const prevTraceCollapsedRef = useRef(layout.traceCollapsed);

  const activeSessionId = activeSession?.session_id || "";
  const running = Boolean(activeSessionId && runningSessionIds[activeSessionId]);
  const activeSessionListRecord = useMemo(
    () => flattenSessionGroups(sessionGroups).find((session) => session.session_id === activeSessionId) || null,
    [sessionGroups, activeSessionId],
  );
  const activeSessionListSignature = sessionFreshnessSignature(activeSessionListRecord);
  const visibleSessionGroups = useMemo(
    () => applyRunningSessionStatus(sessionGroups, runningSessionIds),
    [sessionGroups, runningSessionIds],
  );
  const visibleTraceEvents = useMemo(() => [...traceEvents, ...liveEvents], [traceEvents, liveEvents]);
  const visibleActivityEvents = useMemo(
    () => dedupeTraceEvents([...sessionActivity.events, ...liveEvents]),
    [sessionActivity.events, liveEvents],
  );

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    const matchMedia = globalThis.window?.matchMedia?.bind(globalThis.window);
    if (!matchMedia) {
      return undefined;
    }
    const compactLayout = matchMedia(COMPACT_LAYOUT_QUERY);
    const syncCompactLayout = (event) => {
      setLayout((current) => {
        const saved = loadWorkbenchPreferences().layout;
        const restored = !event.matches && current.compact && saved && typeof saved.historyCollapsed === "boolean" && typeof saved.traceCollapsed === "boolean"
          ? saved : current;
        return { ...adaptLayoutToCompactViewport(restored, event.matches), compact: event.matches };
      });
    };
    syncCompactLayout(compactLayout);
    compactLayout.addEventListener("change", syncCompactLayout);
    return () => compactLayout.removeEventListener("change", syncCompactLayout);
  }, []);

  const setSessionRunning = useCallback((sessionId, isRunning) => {
    if (!sessionId) {
      return;
    }
    const next = isRunning
      ? { ...runningSessionIdsRef.current, [sessionId]: true }
      : omitKey(runningSessionIdsRef.current, sessionId);
    runningSessionIdsRef.current = next;
    setRunningSessionIds(next);
  }, []);

  const loadTrace = useCallback(
    async (session, token = traceLoadTokenRef.current) => {
      if (!session?.session_id) {
        return;
      }
      const runId = session.last_case_run_id || "";
      const [traceResponse, activityResponse] = await Promise.all([
        runId
          ? api
            .getTraceEvents(session.session_id, runId, {
              eventLimit: TRACE_EVENT_LIMIT,
              // Collapsed panel skips the snapshot payloads entirely: every
              // journal snapshot row carries a full request context.
              contextSnapshotLimit: traceCollapsedRef.current ? 0 : CONTEXT_SNAPSHOT_LIMIT,
            })
            .catch((err) => {
              // A turn that just started has no run directory yet; the periodic
              // refresh retries, so a missing trace is not a user-facing error.
              if (err?.status === 404) {
                return null;
              }
              throw err;
            })
          : Promise.resolve(null),
        api
          .getSessionActivity(session.session_id, { eventLimit: TRACE_EVENT_LIMIT })
          .catch((err) => {
            if (err?.status === 404) {
              return null;
            }
            throw err;
          }),
      ]);
      if (token !== traceLoadTokenRef.current) {
        return;
      }
      if (traceResponse) {
        setTraceEvents(traceResponse.events || []);
        setContextSnapshots((current) => mergeContextSnapshots(traceResponse.context_snapshots || [], current));
        setTraceWindow({
          shown: (traceResponse.events || []).length,
          total: traceResponse.events_total || 0,
          truncated: Boolean(traceResponse.events_truncated),
        });
      } else {
        // The run window is unknown while a turn is starting (404) or the
        // session has no run at all; the session-wide activity feed still
        // shows the full tool history either way.
        setTraceEvents([]);
        setTraceWindow(EMPTY_TRACE_WINDOW);
        setContextSnapshots([]);
      }
      setSessionActivity(activityResponse
        ? {
            events: activityResponse.events || [],
            shown: (activityResponse.events || []).length,
            total: activityResponse.events_total || 0,
            truncated: Boolean(activityResponse.events_truncated),
          }
        : EMPTY_SESSION_ACTIVITY);
    },
    [api],
  );

  // A collapsed-panel load skips context snapshots; expanding refetches them
  // for the open session.
  useEffect(() => {
    const wasCollapsed = prevTraceCollapsedRef.current;
    prevTraceCollapsedRef.current = layout.traceCollapsed;
    if (!wasCollapsed || layout.traceCollapsed || !activeSession?.session_id) {
      return;
    }
    void loadTrace(activeSession, traceLoadTokenRef.current);
  }, [activeSession, layout.traceCollapsed, loadTrace]);

  const loadSession = useCallback(
    async (sessionId, previewSession = null, { resumeExecution = true } = {}) => {
      const loadStartedAt = globalThis.performance?.now?.() ?? Date.now();
      const sessionToken = sessionLoadTokenRef.current + 1;
      sessionLoadTokenRef.current = sessionToken;
      traceLoadTokenRef.current += 1;
      const traceToken = traceLoadTokenRef.current;
      const cachedView = sessionViewCacheRef.current.get(sessionId);
      const visiblePreviewSession = previewSession || cachedView?.session || null;
      if (visiblePreviewSession) {
        activeSessionIdRef.current = visiblePreviewSession.session_id || sessionId;
        setActiveSession(visiblePreviewSession);
      }
      setStatus("Loading");
      setNotice("");
      const pendingMessages = pendingSessionMessagesRef.current.get(sessionId);
      const previewMessages = selectSessionMessages(pendingMessages, cachedView?.messages || []);
      const previewLiveEvents = sessionLiveEventsRef.current.get(sessionId) || [];
      messagesRef.current = previewMessages;
      setMessages(previewMessages);
      setTraceEvents([]);
      setTraceWindow(EMPTY_TRACE_WINDOW);
      setSessionActivity(EMPTY_SESSION_ACTIVITY);
      setLiveEvents(previewLiveEvents);
      setContextSnapshots([]);
      let detail;
      try {
        detail = await api.getSession(sessionId, {
          scopeId: previewSession?.scope_id,
          historyLimit: SESSION_HISTORY_LIMIT,
        });
      } catch (err) {
        if (sessionToken !== sessionLoadTokenRef.current) {
          return;
        }
        if (err?.status !== 404) {
          throw err;
        }
        activeSessionIdRef.current = "";
        messagesRef.current = [];
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setTraceWindow(EMPTY_TRACE_WINDOW);
        setSessionActivity(EMPTY_SESSION_ACTIVITY);
        setLiveEvents([]);
        setContextSnapshots([]);
        setNotice("当前项目中找不到此对话，请选择其他对话或新建一个。");
        setStatus("Ready");
        return;
      }
      if (sessionToken !== sessionLoadTokenRef.current) {
        return;
      }
      const nextMessages = historyToMessages(detail.history || []);
      if (sessionToken !== sessionLoadTokenRef.current) {
        return;
      }
      const latestPendingMessages = pendingSessionMessagesRef.current.get(sessionId);
      const liveTranscriptShown = Array.isArray(latestPendingMessages) && latestPendingMessages.length > 0;
      const resolvedMessages = selectSessionMessages(latestPendingMessages, nextMessages);
      activeSessionIdRef.current = detail.session.session_id || sessionId;
      const loadedSession = sessionFromDetail(detail);
      setActiveSession(loadedSession);
      messagesRef.current = resolvedMessages;
      setMessages(resolvedMessages);
      setPendingSteerings((items) => reconcilePendingSteerings(items, sessionId, resolvedMessages));
      // The collapse token folds a freshly rendered historical view; a session
      // whose live transcript is still shown (kept after an interrupt) keeps
      // its expansion state instead.
      if (!liveTranscriptShown) {
        setActivityCollapseToken((value) => value + 1);
      }
      rememberSessionView(sessionViewCacheRef.current, sessionId, {
        session: loadedSession,
        messages: resolvedMessages,
      });
      setStatus(runningSessionIdsRef.current[sessionId] ? "Running" : "Ready");
      if (resumeExecution && detail.runtime_execution_id && !runningSessionIdsRef.current[sessionId]) {
        resumeSessionRef.current?.(detail);
      }
      window.setTimeout(() => {
        loadTrace(loadedSession, traceToken).catch((err) => {
          if (traceToken === traceLoadTokenRef.current) {
            setError(readableError(err));
          }
        });
      }, 0);
      logSessionSwitchPerformance({
        sessionId,
        startedAt: loadStartedAt,
        historyCount: resolvedMessages.length,
        historyTotal: detail.history_total ?? resolvedMessages.length,
      });
    },
    [api, loadTrace],
  );

  const refreshWorkbench = useCallback(
    async ({ selectSessionId, selectFirst = false, preserveSessionId = "", resumeExecution = true } = {}) => {
      setError("");
      const [nextSettings, nextProjects, nextSessionGroups] = await Promise.all([
        api.getSettings(),
        api.listProjects(),
        sessionGroupSync.refresh(),
      ]);
      const groups = nextSessionGroups.groups || [];
      const sessionList = flattenSessionGroups(groups);
      const activeScopeId = nextSessionGroups.active_scope_id || scopeIdFromWorkspace(nextSettings.workspace_root);
      setSettings(nextSettings);
      setProjects(nextProjects.projects || []);
      const targetSessionId =
        selectSessionId ||
        (selectFirst ? firstSessionIdInScope(groups, activeScopeId) : preserveSessionId);
      const targetSession = sessionList.find((session) => session.session_id === targetSessionId);
      setActiveScopeId(targetSession?.scope_id || activeScopeId);
      if (targetSessionId && targetSession) {
        await loadSession(targetSessionId, targetSession, { resumeExecution });
      } else {
        sessionLoadTokenRef.current += 1;
        traceLoadTokenRef.current += 1;
        activeSessionIdRef.current = "";
        messagesRef.current = [];
        setActiveSession(null);
        setMessages([]);
        setTraceEvents([]);
        setTraceWindow(EMPTY_TRACE_WINDOW);
        setSessionActivity(EMPTY_SESSION_ACTIVITY);
        setLiveEvents([]);
        setContextSnapshots([]);
      }
      if (!runningSessionIdsRef.current[activeSessionIdRef.current]) setStatus("Ready");
    },
    [api, loadSession, sessionGroupSync],
  );

  useEffect(() => {
    let cancelled = false;
    setStatus("Connecting");
    initializeWorkbench(() => {
      if (cancelled) {
        return undefined;
      }
      return refreshWorkbench({ selectFirst: true });
    }).catch((err) => {
      if (!cancelled) {
        setStatus("Offline");
        setError(readableError(err));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [refreshWorkbench]);

  const handleCreateSession = useCallback(async (scope = null) => {
    try {
      setError("");
      setNotice("");
      const targetWorkspace = scope?.workspace_root || "";
      if (targetWorkspace && projectPathKey(targetWorkspace) !== projectPathKey(settings.workspace_root)) {
        setStatus("Opening project");
        await api.updateSettings({ workspaceRoot: targetWorkspace, agent: "" });
      }
      // Enter the empty new-chat state instead of creating a session up front:
      // the composer shows the model-group selector there, and the first
      // message creates the session with the chosen group.
      setPendingNewSessionScope(scope?.scope_id === "conversation" ? "conversation" : "");
      // Show the empty composer before the sidebar refresh resolves: the
      // groups read can stall behind a running turn, and awaiting it first
      // made new chats feel slow to appear.
      sessionLoadTokenRef.current += 1;
      traceLoadTokenRef.current += 1;
      activeSessionIdRef.current = "";
      messagesRef.current = [];
      setActiveSession(null);
      setMessages([]);
      setTraceEvents([]);
      setTraceWindow(EMPTY_TRACE_WINDOW);
      setSessionActivity(EMPTY_SESSION_ACTIVITY);
      setLiveEvents([]);
      setContextSnapshots([]);
      setStatus("Ready");
      setNotice("已进入新建会话：选择模型组后，发送第一条消息即创建会话。");
      await refreshWorkbench({ preserveSessionId: "" });
    } catch (err) {
      setError(readableError(err));
    }
  }, [api, refreshWorkbench, settings.workspace_root]);

  const handleDeleteSession = useCallback(
    async (sessionId, scope) => {
      try {
        setError("");
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
        }
        await api.deleteSession(sessionId, { scopeId: scope?.scope_id });
        pendingSessionMessagesRef.current.delete(sessionId);
        sessionViewCacheRef.current.delete(sessionId);
        sessionLiveEventsRef.current.delete(sessionId);
        composerDraftsRef.current.delete(sessionId);
        await refreshWorkbench({ selectFirst: true });
        setNotice("Session deleted");
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, refreshWorkbench, settings.workspace_root],
  );

  const handleForkSession = useCallback(
    async (sessionId, scope, atSequence) => {
      try {
        setError("");
        setNotice("");
        const forked = await api.forkSession(sessionId, {
          scopeId: scope?.scope_id,
          atSequence,
        });
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
        }
        await refreshWorkbench({ selectSessionId: forked.session_id });
        setNotice("Session forked");
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, refreshWorkbench, settings.workspace_root],
  );

  const handleDeleteProject = useCallback(async (scope) => {
    if (!scope?.scope_id) return;
    try {
      setError("");
      setNotice("");
      if (scope.scope_id === scopeIdFromWorkspace(settings.workspace_root)) {
        const fallback = projects.find(
          (project) => projectPathKey(project.workspace_root) !== projectPathKey(scope.workspace_root),
        );
        if (!fallback) {
          throw new Error("Open another project before removing this one.");
        }
        setStatus("Opening project");
        await api.updateSettings({ workspaceRoot: fallback.workspace_root, agent: "" });
      }
      await api.removeProject(scope.scope_id);
      await refreshWorkbench({ selectFirst: true });
      setNotice("Project removed from sidebar");
    } catch (err) {
      setError(readableError(err));
    }
  }, [api, projects, refreshWorkbench, settings.workspace_root]);

  const handleReorderProjects = useCallback(async (orderedScopeIds) => {
    setSessionGroups((current) => {
      const byScopeId = new Map(current.map((group) => [group.scope?.scope_id, group]));
      const next = orderedScopeIds.map((scopeId) => byScopeId.get(scopeId)).filter(Boolean);
      for (const group of current) {
        if (!orderedScopeIds.includes(group.scope?.scope_id)) next.push(group);
      }
      return next;
    });
    try {
      await api.updateProjectOrder(orderedScopeIds);
    } catch (err) {
      setError(readableError(err));
      sessionGroupSync.refresh().catch(() => {});
    }
  }, [api, sessionGroupSync]);

  const handleReorderSessions = useCallback(async (scopeId, orderedSessionIds) => {
    setSessionGroups((current) => current.map((group) => {
      if (group.scope?.scope_id !== scopeId) return group;
      const sessions = group.sessions || [];
      const byId = new Map(sessions.map((session) => [session.session_id, session]));
      const next = orderedSessionIds.map((id) => byId.get(id)).filter(Boolean);
      for (const session of sessions) {
        if (!orderedSessionIds.includes(session.session_id)) next.push(session);
      }
      return { ...group, sessions: next };
    }));
    try {
      await api.updateSessionOrder(scopeId, orderedSessionIds);
    } catch (err) {
      setError(readableError(err));
      sessionGroupSync.refresh().catch(() => {});
    }
  }, [api, sessionGroupSync]);

  const handleToggleSessionPin = useCallback(async (session, scope, pinned) => {
    const scopeId = scope?.scope_id || "";
    setSessionGroups((current) => current.map((group) => (
      group.scope?.scope_id !== scopeId ? group : {
        ...group,
        sessions: (group.sessions || []).map((item) => (
          item.session_id === session.session_id ? { ...item, pinned } : item
        )),
      }
    )));
    try {
      await api.updateSessionPin(session.session_id, { pinned, scopeId });
    } catch (err) {
      setError(readableError(err));
      sessionGroupSync.refresh().catch(() => {});
    }
  }, [api, sessionGroupSync]);

  const refreshAutomations = useCallback(async () => {
    try {
      const result = await api.listAutomations();
      const next = result.automations || [];
      setAutomations((current) => JSON.stringify(current) === JSON.stringify(next) ? current : next);
    } catch {
      // The sidebar keeps the last known list; the scheduled view surfaces errors.
    }
  }, [api]);
  useEffect(() => { refreshAutomations(); }, [refreshAutomations, activeView]);
  useEffect(() => {
    const timer = setInterval(() => refreshAutomations(), 60000);
    return () => clearInterval(timer);
  }, [refreshAutomations]);

  const handleToggleAutomation = useCallback(async (item) => {
    try {
      if (item.status === "active") await api.pauseAutomation(item.automation_id);
      else if (item.status === "paused") await api.resumeAutomation(item.automation_id);
      await refreshAutomations();
    } catch (err) {
      setError(readableError(err));
    }
  }, [api, refreshAutomations]);

  const handleSelectSession = useCallback(
    async (session, scope) => {
      setActiveView("chat");
      const sessionId = typeof session === "string" ? session : session?.session_id;
      if (!sessionId) {
        return;
      }
      try {
        setError("");
        if (scope?.workspace_root && scope.workspace_root !== settings.workspace_root) {
          await api.updateSettings({ workspaceRoot: scope.workspace_root });
          await refreshWorkbench({ selectSessionId: sessionId });
          return;
        }
        await loadSession(sessionId, typeof session === "string" ? null : session);
      } catch (err) {
        setError(readableError(err));
      }
    },
    [api, loadSession, refreshWorkbench, settings.workspace_root],
  );

  const handleSendMessage = useCallback(
    async (message, recovery = null, attachments = []) => {
      const initialSessionId = recovery?.session.session_id || activeSessionIdRef.current;
      if ((!recovery && !message.trim() && !attachments.length) || (initialSessionId && runningSessionIdsRef.current[initialSessionId])) {
        return;
      }
      if (initialSessionId) {
        setSessionRunning(initialSessionId, true);
      }
      setStatus(recovery ? "Reconnecting" : "Running");
      setError("");
      setNotice("");
      setTraceEvents([]);
      setTraceWindow(EMPTY_TRACE_WINDOW);
      setLiveEvents([]);
      if (!initialSessionId) {
        setContextSnapshots([]);
      }
      const assistantId = `assistant-${Date.now()}`;
      let streamSessionId = initialSessionId || null;
      let streamMessages = messagesRef.current;
      let streamEvents = [];
      const streamScopeId = recovery?.session.scope_id || activeSession?.scope_id || pendingNewSessionScope || undefined;
      const startedWithoutSession = !streamSessionId;
      // Creating the session takes a round trip; if the user navigates before
      // the started event arrives, that session must not pull the view back.
      const initialLoadToken = sessionLoadTokenRef.current;
      let finalStatus = "Ready";
      let streamError = "";
      // Track the active assistant message id to support split-on-steering.
      let activeAssistantId = assistantId;

      // A stream that has not bound a session yet belongs to the new-chat view
      // it started in; after lara.chat.started only that session follows it.
      const isStreamSessionVisible = () => {
        const currentSessionId = activeSessionIdRef.current;
        return streamSessionId
          ? currentSessionId === streamSessionId
          : startedWithoutSession && !currentSessionId;
      };

      const updateCachedMessages = (updater) => {
        if (!streamSessionId) {
          return;
        }
        const current = pendingSessionMessagesRef.current.get(streamSessionId) || streamMessages;
        const next = updater(current);
        streamMessages = next;
        pendingSessionMessagesRef.current.set(streamSessionId, next);
      };

      // Re-attaching to a running execution replays its journal from the
      // start (no attach cursor yet — see the comment at the streamChat call
      // below). Replayed events still project into the cache immediately,
      // but the view only paints on the slow flush cadence, so the backlog
      // does not re-stream through the transcript.
      let replayCatchup = Boolean(recovery);
      let replayFlushTimer = null;
      const flushReplayView = () => {
        replayFlushTimer = null;
        if (isStreamSessionVisible()) {
          setMessages(streamMessages);
          setLiveEvents(streamEvents);
        }
      };
      const scheduleReplayViewFlush = () => {
        if (replayFlushTimer !== null) {
          return;
        }
        replayFlushTimer = globalThis.setTimeout(flushReplayView, REPLAY_VIEW_FLUSH_MS);
      };
      const endReplayCatchup = () => {
        const wasReplaying = replayCatchup;
        replayCatchup = false;
        if (replayFlushTimer !== null) {
          globalThis.clearTimeout(replayFlushTimer);
          replayFlushTimer = null;
        }
        return wasReplaying;
      };

      const updateVisibleMessages = (updater) => {
        updateCachedMessages(updater);
        if (!isStreamSessionVisible()) {
          return;
        }
        if (replayCatchup) {
          scheduleReplayViewFlush();
          return;
        }
        setMessages(updater);
      };

      // Stream deltas arrive in bursts faster than frames: buffer them and
      // apply once per animation frame so the transcript renders at display
      // cadence instead of once per SSE event.
      let pendingDeltaEvents = [];
      let deltaFlushQueued = false;
      const flushDeltaEvents = () => {
        deltaFlushQueued = false;
        if (!pendingDeltaEvents.length) {
          return;
        }
        const events = pendingDeltaEvents;
        pendingDeltaEvents = [];
        for (const event of events) {
          projectLiveExecutionEvent(updateVisibleMessages, activeAssistantId, event);
        }
      };
      const scheduleDeltaFlush = () => {
        if (deltaFlushQueued) {
          return;
        }
        deltaFlushQueued = true;
        requestAnimationFrame(flushDeltaEvents);
      };

      const nextMessages = recovery ? messagesForRecovery(recovery, assistantId) : [
        ...messagesRef.current,
        {
          id: `user-${Date.now()}`,
          role: "user",
          content: message,
          createdAt: Date.now(),
          attachments: attachments.length ? attachments : undefined,
        },
        {
          id: assistantId,
          role: "assistant",
          content: "",
          status: "running",
          startedAt: Date.now(),
          endedAt: null,
          sections: [],
        },
      ];
      if (initialSessionId) {
        pendingSessionMessagesRef.current.set(initialSessionId, nextMessages);
        sessionLiveEventsRef.current.set(initialSessionId, streamEvents);
      }
      streamMessages = nextMessages;
      messagesRef.current = nextMessages;
      setMessages(nextMessages);

      try {
        // Recovery attaches without afterSequence, and pygent then subscribes
        // from the execution's first event: the whole journal streams back
        // before the live tail. The replay-catchup mode around
        // updateVisibleMessages only hides the re-streaming visuals; the
        // transfer and projection cost remains. If that cost matters, the
        // fix is a protocol change: an attach cursor (last-seen sequence or
        // turn-start marker) honored by the server and sent by this client.
        await api.streamChat(
          {
            message,
            executionId: recovery?.runtime_execution_id,
            sessionId: streamSessionId,
            caseId: "chat",
            modelGroupName: pendingNewModelGroup || settings.default_model_group || undefined,
            scopeId: streamScopeId,
            attachments: attachments.length ? attachments : undefined,
          },
          {
            onConnectionState: (state) => {
              if (state === "reconnecting" && streamSessionId) {
                setSteeringSessionIds((items) => omitKey(items, streamSessionId));
              }
              if (isStreamSessionVisible()) setStatus(state === "reconnecting" ? "Reconnecting" : "Running");
            },
            onEvent: ({ data }) => {
              const eventKind = data.kind || "";
              if (replayCatchup && replayEventAgeMs(data) <= REPLAY_LIVE_THRESHOLD_MS) {
                // A freshly recorded event means the replay reached the live
                // tail: paint the accumulated state once and resume normal
                // streaming cadence.
                endReplayCatchup();
                if (isStreamSessionVisible()) {
                  setMessages(streamMessages);
                  setLiveEvents(streamEvents);
                }
              }
              const eventData = data.data || {};
              const eventSessionId = eventKind === "lara.chat.started" ? String(eventData.session_id || "") : "";
              if (eventSessionId && !streamSessionId) {
                // Read visibility before binding: an unbound stream is visible
                // from the new-chat view, and only while the user stayed there.
                const followSession = isStreamSessionVisible()
                  && sessionLoadTokenRef.current === initialLoadToken;
                streamSessionId = eventSessionId;
                setSessionRunning(eventSessionId, true);
                pendingSessionMessagesRef.current.set(eventSessionId, streamMessages);
                if (followSession) {
                  activeSessionIdRef.current = eventSessionId;
                  setActiveSession((current) => current || {
                    session_id: eventSessionId,
                    scope_id: eventData.scope_id || streamScopeId,
                    model_group_name: pendingNewModelGroup || settings.default_model_group,
                  });
                  // The started event only carries identifiers, and the
                  // background poll sleeps while the turn runs: fetch the
                  // record once so the header and composer show the bound
                  // group and models during the turn.
                  void api.getSession(eventSessionId, { scopeId: streamScopeId }).then((detail) => {
                    if (activeSessionIdRef.current === eventSessionId) {
                      setActiveSession((current) => (current?.session_id === eventSessionId ? sessionFromDetail(detail) : current));
                    }
                  }).catch(() => {});
                }
              }
              const terminalEvent = ["execution.completed", "execution.failed", "execution.deadline_exceeded", "execution.cancelled", "lara.transport.error"].includes(eventKind);
              if (streamSessionId && data.execution_id && !terminalEvent) {
                activeExecutionsRef.current.set(streamSessionId, { executionId: data.execution_id });
                setSteeringSessionIds((items) => items[streamSessionId] ? items : { ...items, [streamSessionId]: true });
              } else if (streamSessionId && terminalEvent) {
                activeExecutionsRef.current.delete(streamSessionId);
                setSteeringSessionIds((items) => omitKey(items, streamSessionId));
                // The terminal event is the authoritative end of the turn (the
                // backend yields it only after the run is durable). Drop the
                // running flag here instead of waiting for the stream close and
                // the post-completion reload, or the composer keeps spinning
                // with a stop button that has nothing left to cancel.
                setSessionRunning(streamSessionId, false);
              }
              streamEvents = appendSessionLiveTraceEvent(
                sessionLiveEventsRef.current,
                streamSessionId,
                streamEvents,
                apiEventToTraceEvent(data),
              );
              if (isStreamSessionVisible()) {
                if (replayCatchup) {
                  scheduleReplayViewFlush();
                } else {
                  setLiveEvents(streamEvents);
                }
              }
              const isStreamDelta = eventKind === "model.text.delta" || eventKind === "model.reasoning.delta";
              if (isStreamDelta) {
                pendingDeltaEvents.push(data);
                scheduleDeltaFlush();
              } else {
                // Queued deltas apply first so structural events (steering
                // split, tool calls, completion) always read current messages.
                flushDeltaEvents();
                projectLiveExecutionEvent(updateVisibleMessages, activeAssistantId, data);
              }
              // The runtime announces the ReAct boundary it drained a steering
              // input on: that is where the transcript splits and the input stops
              // being pending.
              if (eventKind === "lara.runtime.message" && eventData.role === "user" && eventData.kind === "lara.user.steering") {
                const inputId = String(eventData.data?.input_id || "");
                const newAssistantId = `assistant-${Date.now()}`;
                const nextMessages = splitAssistantForSteering(
                  streamMessages, activeAssistantId, inputId, String(eventData.content || ""), newAssistantId,
                  Date.now(), eventData.data?.attachments,
                );
                if (nextMessages !== streamMessages) {
                  streamMessages = nextMessages;
                  updateCachedMessages(() => nextMessages);
                  messagesRef.current = nextMessages;
                  if (isStreamSessionVisible()) setMessages(() => nextMessages);
                  activeAssistantId = newAssistantId;
                }
                setPendingSteerings((items) => resolvePendingSteering(items, streamSessionId, inputId));
              }
              if (eventKind === "lara.approval.requested") {
                setApprovals((items) => [
                  ...items.filter((item) => item.approval_id !== eventData.approval_id),
                  { ...eventData, session_id: eventSessionId || streamSessionId },
                ]);
              } else if (eventKind === "execution.completed") {
                finalStatus = "Done";
                if (isStreamSessionVisible()) {
                  setActivityCollapseToken((value) => value + 1);
                }
              } else if (eventKind === "lara.transport.error" || eventKind === "execution.failed" || eventKind === "execution.deadline_exceeded") {
                finalStatus = "Error";
                streamError = eventData.error ? readableError(eventData.error) : "执行未能恢复或已经失败，可保留历史并发送新消息。";
                if (isStreamSessionVisible()) {
                  setActivityCollapseToken((value) => value + 1);
                }
              } else if (eventKind === "execution.cancelled") {
                // An interrupt is not a finished task: the transcript stays
                // expanded, so the collapse token must not move here.
                finalStatus = "Skipped";
              }
              if (["execution.completed", "execution.failed", "execution.deadline_exceeded", "execution.cancelled"].includes(eventKind)) {
                setApprovals((items) => items.filter((item) => item.session_id !== streamSessionId));
                // No boundary reported for these inputs: the turn ended first.
                setPendingSteerings((items) => stalePendingSteerings(items, streamSessionId));
              }
            },
          },
        );
        const wasReplaying = endReplayCatchup();
        if (wasReplaying && isStreamSessionVisible()) {
          setMessages(streamMessages);
          setLiveEvents(streamEvents);
        }
        flushDeltaEvents();
        const currentSessionId = activeSessionIdRef.current;
        if (streamSessionId) {
          if (finalStatus === "Skipped") {
            // A cancelled turn keeps the live transcript on screen: rebuilding
            // from history would remount every activity block collapsed.
            pendingSessionMessagesRef.current.set(streamSessionId, streamMessages);
          } else {
            pendingSessionMessagesRef.current.delete(streamSessionId);
          }
        }
        if (streamSessionId && currentSessionId === streamSessionId) {
          await refreshWorkbench({ resumeExecution: false, selectSessionId: streamSessionId });
        } else {
          // The user moved to another chat while this ran, or the turn never
          // created a session: refresh the sidebar and keep the current view.
          await sessionGroupSync.refresh();
        }
        if (isStreamSessionVisible()) {
          setStatus(finalStatus);
          if (streamError) setError(streamError);
        }
      } catch (err) {
        if (isStreamSessionVisible()) {
          setStatus("Error");
        }
        setError(readableError(err));
        flushDeltaEvents();
        projectLiveExecutionEvent(updateVisibleMessages, activeAssistantId, {
          kind: "lara.transport.error",
          data: { error: readableError(err) },
        });
        if (isStreamSessionVisible()) {
          setActivityCollapseToken((value) => value + 1);
        }
      } finally {
        const completedSessionId = streamSessionId || initialSessionId;
        if (completedSessionId) {
          activeExecutionsRef.current.delete(completedSessionId);
          setSteeringSessionIds((items) => omitKey(items, completedSessionId));
          setSessionRunning(completedSessionId, false);
        }
      }
    },
    [activeSession?.scope_id, api, pendingNewModelGroup, pendingNewSessionScope, refreshWorkbench, sessionGroupSync, setSessionRunning, settings.default_model_group],
  );
  resumeSessionRef.current = (detail) => handleSendMessage("", detail);

  useEffect(() => {
    if (!activeSessionId || !activeSessionListRecord || running) return undefined;
    if (!activeSessionNeedsRefresh(activeSession, activeSessionListRecord)
      && !activeSessionRunAwaitingAttach(
        activeSession,
        activeSessionListRecord,
        runningSessionIdsRef.current,
      )) return undefined;
    const controller = new AbortController();
    void (async () => {
      const { signal } = controller;
      const token = sessionLoadTokenRef.current;
      const traceToken = traceLoadTokenRef.current;
      const isCurrent = () => activeSessionIdRef.current === activeSessionId
        && sessionLoadTokenRef.current === token
        && traceLoadTokenRef.current === traceToken
        && !runningSessionIdsRef.current[activeSessionId];
      await refreshActiveSession({
        api, sessionId: activeSessionId, scopeId: activeSession?.scope_id, signal, isCurrent,
        includeContextSnapshots: !traceCollapsedRef.current,
        onResume: (detail) => {
          traceLoadTokenRef.current += 1;
          setActiveSession(sessionFromDetail(detail));
          resumeSessionRef.current?.(detail);
        },
        onSnapshot: (detail, trace, activity) => {
          traceLoadTokenRef.current += 1;
          const loadedSession = sessionFromDetail(detail);
          const activeSessionSignature = sessionDetailSignature(loadedSession);
          if (activeSessionSignatureRef.current !== activeSessionSignature) {
            activeSessionSignatureRef.current = activeSessionSignature;
            setActiveSession(loadedSession);
          }
          const traceEventsSignature = traceResponseSignature(trace, "events");
          if (traceEventsSignatureRef.current !== traceEventsSignature) {
            traceEventsSignatureRef.current = traceEventsSignature;
            setTraceEvents(trace.events || []);
            setTraceWindow({
              shown: (trace.events || []).length,
              total: trace.events_total || 0,
              truncated: Boolean(trace.events_truncated),
            });
          }
          const contextSnapshotsSignature = traceResponseSignature(trace, "context_snapshots");
          if (contextSnapshotsSignatureRef.current !== contextSnapshotsSignature) {
            contextSnapshotsSignatureRef.current = contextSnapshotsSignature;
            setContextSnapshots(trace.context_snapshots || []);
          }
          const activitySignature = traceResponseSignature(activity, "events");
          if (activitySignatureRef.current !== activitySignature) {
            activitySignatureRef.current = activitySignature;
            setSessionActivity({
              events: activity.events || [],
              shown: (activity.events || []).length,
              total: activity.events_total || 0,
              truncated: Boolean(activity.events_truncated),
            });
          }
          sessionLiveEventsRef.current.delete(activeSessionId);
          setLiveEvents((current) => current.length ? [] : current);
          const messagesHistorySignature = sessionHistorySignature(detail);
          const pendingLiveMessages = pendingSessionMessagesRef.current.get(activeSessionId);
          // A transcript this client still owns (a running turn, or one kept
          // on screen after an interrupt) wins over the persisted history
          // snapshot, or the rebuild would remount every activity collapsed.
          if (messagesHistorySignatureRef.current !== messagesHistorySignature
            && !(Array.isArray(pendingLiveMessages) && pendingLiveMessages.length > 0)) {
            messagesHistorySignatureRef.current = messagesHistorySignature;
            const nextMessages = historyToMessages(detail.history || []);
            messagesRef.current = nextMessages;
            setMessages(nextMessages);
            setPendingSteerings((items) => reconcilePendingSteerings(items, activeSessionId, nextMessages));
            rememberSessionView(sessionViewCacheRef.current, activeSessionId, {
              session: loadedSession,
              messages: nextMessages,
            });
          }
        },
      });
    })().catch(() => {});
    return () => controller.abort();
  }, [api, activeSessionId, activeSession?.scope_id, activeSessionListSignature, running]);

  const handleSteering = useCallback(async (message, inputId, attachments = []) => {
    const sessionId = activeSessionIdRef.current;
    const execution = activeExecutionsRef.current.get(sessionId);
    if (!execution || !runningSessionIdsRef.current[sessionId]) {
      throw new Error("执行尚未连接或已经结束，请稍后重试。");
    }
    await api.steerChat(execution.executionId, { sessionId, inputId, message, attachments });
    // Show the input right away, but keep it pending: the runtime decides where
    // the ReAct boundary falls, and only its boundary event moves the activity
    // and places the input into the transcript.
    setPendingSteerings((items) => addPendingSteering(items, sessionId, { inputId, text: message, attachments }));
    if (activeSessionIdRef.current === sessionId) {
      setNotice("指令已送达，将打断当前步骤并立即生效。");
    }
  }, [api]);

  const handleCancelTurn = useCallback(async () => {
    const sessionId = activeSessionIdRef.current;
    const execution = activeExecutionsRef.current.get(sessionId);
    if (!runningSessionIdsRef.current[sessionId]) {
      // The turn already ended (the terminal event dropped the running flag);
      // this click raced the stop button unmounting. There is nothing to
      // cancel anywhere, so unsticking silently beats reporting an error.
      return;
    }
    if (!execution) {
      // Still running locally but no execution is bound yet: the stream is
      // connecting (new turn or resume), so nothing has been submitted that
      // this API could cancel. Say that instead of implying the task ended.
      throw new Error("任务尚未连接到执行，暂无可取消的内容。");
    }
    await api.cancelChatTurn(execution.executionId, { sessionId });
  }, [api]);

  const handleSaveSettings = useCallback(
    async (draft) => {
      if (settingsSaveBusyRef.current) return;
      settingsSaveBusyRef.current = true;
      setSavingSettings(true);
      try {
        setError("");
        setStatus("Reloading");
        const nextSettings = await api.updateSettings(settingsForSave(draft, settings));
        setSettings(nextSettings);
        setSettingsOpen(false);
        const workspaceChanged = projectPathKey(nextSettings.workspace_root) !== projectPathKey(settings.workspace_root);
        await refreshWorkbench({
          selectFirst: workspaceChanged,
          preserveSessionId: workspaceChanged ? "" : activeSessionIdRef.current,
        });
        setNotice("设置已保存，新运行将使用新配置；正在运行的任务继续使用原配置。");
      } catch (err) {
        setStatus("Error");
        setError(readableError(err));
      } finally {
        settingsSaveBusyRef.current = false;
        setSavingSettings(false);
      }
    },
    [api, refreshWorkbench, settings],
  );

  const handleOpenSettings = useCallback(() => setSettingsOpen(true), []);
  const handleOpenScheduled = useCallback(() => setActiveView("scheduled"), []);
  const handleToggleHistory = useCallback(
    () => setLayout((current) => toggleHistory(current, { compact: current.compact })),
    [],
  );
  const handleChooseProject = useCallback(async (newTask = false) => {
    if (projectChooserBusyRef.current) return;
    projectChooserBusyRef.current = true;
    try {
      setError("");
      setNotice("");
      const chooseDirectory = globalThis.window?.laraDesktop?.chooseProjectDirectory;
      if (typeof chooseDirectory !== "function") {
        setError("请在桌面应用中打开项目，以使用系统文件夹选择器。");
        return;
      }
      const workspaceRoot = await chooseDirectory(settings.workspace_root);
      if (!workspaceRoot || projectPathKey(workspaceRoot) === projectPathKey(settings.workspace_root)) return;
      setStatus("Opening project");
      await api.updateSettings({ workspaceRoot, agent: "" });
      if (newTask === true) {
        // Enter the new-chat state; the first message creates the session.
        setPendingNewSessionScope("");
        await refreshWorkbench({ preserveSessionId: "" });
      } else {
        await refreshWorkbench({ selectFirst: true });
      }
      setNotice("Project opened");
    } catch (err) {
      setStatus("Error");
      setError(readableError(err));
    } finally {
      projectChooserBusyRef.current = false;
    }
  }, [api, refreshWorkbench, settings.workspace_root]);

  const appClassName = appLayoutClassName(layout);
  const fittedWidths = fitPanelWidths(panelWidths, layout, workbenchWidth);
  function resizePanel(side, width) {
    const other = side === "history" ? fittedWidths.trace : fittedWidths.history;
    const [minimum, maximum] = PANEL_LIMITS[side];
    const limit = Math.max(minimum, Math.min(maximum, workbenchWidth - other - 320));
    setPanelWidths((current) => ({ ...current, [side]: Math.max(minimum, Math.min(limit, width)) }));
  }

  async function handleApproval(approval, approved) {
    try {
      await api.deliverApproval(
        approval.approval_id,
        approved,
        approved ? "已在 Lara Desktop 批准" : "已在 Lara Desktop 拒绝",
      );
      setApprovals((items) => items.filter((item) => item.approval_id !== approval.approval_id));
    } catch (approvalError) {
      setApprovals((items) => items.filter((item) => item.approval_id !== approval.approval_id));
      setError(String(approvalError?.message || approvalError));
    }
  }

  return (
    <main ref={workbenchRef} aria-label="Workbench" className={`${appClassName}${resizingPanel ? " resizing-panels" : ""}`} style={{ "--history-width": `${fittedWidths.history}px`, "--trace-width": `${fittedWidths.trace}px` }}>
      <div className="workbench-content" hidden={settingsOpen}>
      {!layout.historyCollapsed && <PanelDivider side="history" width={fittedWidths.history} max={Math.max(200, Math.min(480, workbenchWidth - fittedWidths.trace - 320))} onResize={(width) => resizePanel("history", width)} onDragging={setResizingPanel} />}
      {!layout.traceCollapsed && <PanelDivider side="trace" width={fittedWidths.trace} max={Math.max(260, Math.min(720, workbenchWidth - fittedWidths.history - 320))} onResize={(width) => resizePanel("trace", width)} onDragging={setResizingPanel} />}
      <SessionSidebar
        collapsed={layout.historyCollapsed}
        settings={settings}
        projects={projects}
        sessionGroups={visibleSessionGroups}
        automations={automations}
        onToggleAutomation={handleToggleAutomation}
        activeScopeId={activeScopeId}
        activeSessionId={activeSessionId}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        onForkSession={handleForkSession}
        onDeleteProject={handleDeleteProject}
        onSelectSession={handleSelectSession}
        onReorderProjects={handleReorderProjects}
        onReorderSessions={handleReorderSessions}
        onToggleSessionPin={handleToggleSessionPin}
        onChooseProject={handleChooseProject}
        onOpenSettings={handleOpenSettings}
        onOpenScheduled={handleOpenScheduled}
        scheduledActive={activeView === "scheduled"}
        onToggle={handleToggleHistory}
      />
      {activeView === "scheduled" ? <ScheduledPage
        api={api}
        projects={projects}
        activeSession={activeSession}
        settings={settings}
        onOpenSession={(sessionId, workspaceRoot) => handleSelectSession(sessionId, { workspace_root: workspaceRoot })}
      /> : <ChatPane
        key={activeSessionId || "new-chat"}
        composerDrafts={composerDraftsRef.current}
        activeSession={activeSession}
        messages={messages}
        activityCollapseToken={activityCollapseToken}
        settings={settings}
        status={status}
        running={running}
        steeringReady={Boolean(steeringSessionIds[activeSessionId])}
        approvals={approvals.filter((item) => item.session_id === activeSessionId)}
        pendingSteerings={pendingSteerings[activeSessionId] || []}
        api={api}
        onSendMessage={handleSendMessage}
        onForkSession={handleForkSession}
        onSteering={handleSteering}
        onCancelTurn={handleCancelTurn}
        onApproval={handleApproval}
        projects={projects}
        onSelectProject={handleCreateSession}
        onChooseProject={() => handleChooseProject(true)}
        onChangePermissions={async (approvalsEnabled) => {
          const nextSettings = await api.updateSettings({ approvalsEnabled });
          setSettings(nextSettings);
        }}
        pendingNewModelGroup={pendingNewModelGroup}
        onChangeNewModelGroup={setPendingNewModelGroup}
        pendingNewSessionScope={pendingNewSessionScope}
        onChangeModel={async (selectedModelKey) => {
          if (!activeSessionIdRef.current) return;
          await api.updateSessionModel(activeSessionIdRef.current, selectedModelKey, { scopeId: activeSession?.scope_id });
          await loadSession(activeSessionIdRef.current, activeSession, { resumeExecution: false });
        }}
      />}
      {activeView === "chat" ? <TracePanel
        api={api}
        activeSession={activeSession}
        collapsed={layout.traceCollapsed}
        contextSnapshots={contextSnapshots}
        events={visibleTraceEvents}
        activityEvents={visibleActivityEvents}
        activityMeta={sessionActivity}
        traceMeta={traceWindow}
        settings={settings}
        onToggle={() =>
          setLayout((current) => toggleTrace(current, { compact: current.compact }))
        }
      /> : <aside className="trace automation-aside"><CalendarClock size={26} /><strong>后台运行</strong><p>Lara Desktop 保持运行时，任务会在计划时间自动开始。</p></aside>}
      </div>
      {(error || notice) && (
        <div className={error ? "toast error" : "toast"} role="status">
          {error || notice}
        </div>
      )}
      {settingsOpen && (
        <SettingsPanel
          settings={settings}
          api={api}
          disabled={savingSettings}
          onClose={() => setSettingsOpen(false)}
          onSave={handleSaveSettings}
        />
      )}
    </main>
  );
}

function PanelDivider({ side, width, max, onResize, onDragging }) {
  const dragRef = useRef(null);
  function stop(event) {
    if (!dragRef.current) return;
    dragRef.current = null;
    onDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return <div className={`panel-divider panel-divider-${side}`} role="separator" aria-label={side === "history" ? "调整项目面板宽度" : "调整详情面板宽度"}
    aria-orientation="vertical" aria-valuemin={PANEL_LIMITS[side][0]} aria-valuemax={max} aria-valuenow={width} tabIndex={0}
    title="拖动调整宽度，双击恢复默认；方向键微调"
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.currentTarget.focus();
      dragRef.current = { x:event.clientX, width };
      event.currentTarget.setPointerCapture(event.pointerId);
      onDragging(true);
    }}
    onPointerMove={(event) => {
      if (dragRef.current) onResize(dragRef.current.width + (event.clientX - dragRef.current.x) * (side === "history" ? 1 : -1));
    }} onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop}
    onDoubleClick={() => onResize(DEFAULT_PANEL_WIDTHS[side])}
    onKeyDown={(event) => {
      if (event.key === "Home") { event.preventDefault(); onResize(DEFAULT_PANEL_WIDTHS[side]); }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        onResize(width + (event.key === "ArrowRight" ? 1 : -1) * (side === "history" ? 1 : -1) * (event.shiftKey ? 40 : 10));
      }
    }} />;
}

export function appLayoutClassName(layout) {
  return [
    "app-shell",
    layout.compact ? "compact-layout" : "",
    layout.historyCollapsed ? "history-collapsed" : "",
    layout.traceCollapsed ? "trace-collapsed" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export const MAX_VISIBLE_SESSIONS = 5;
// The recents section shows a short default list and a wider expanded one;
// pinned sessions always lead it and occupy slots like any other entry. The
// pool itself never exceeds RECENT_MAX_SESSIONS, so "更多" stays truthful.
export const RECENT_VISIBLE_SESSIONS = 5;
export const RECENT_MAX_SESSIONS = 20;

// Merge every scope's sessions into the recents pool: pinned lead in their
// pin order, the rest follows by recency, then the pool is capped.
export function buildRecentConversations(sessionGroups, search) {
  const items = sessionGroups.flatMap((group) => (group.sessions || []).map((session) => ({ session, scope: group.scope })));
  const matches = (item) => `${item.session.title || ""} ${item.session.session_id || ""} ${item.scope?.label || ""}`.toLocaleLowerCase().includes(search);
  const pool = search ? items.filter(matches) : items;
  const recency = (item) => {
    const parsed = Date.parse(item.session.updated_at || "");
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const byRecency = (a, b) => recency(b) - recency(a);
  return [
    ...pool.filter((item) => item.session.pinned).sort(byRecency),
    ...pool.filter((item) => !item.session.pinned).sort(byRecency),
  ].slice(0, RECENT_MAX_SESSIONS);
}

// Insert `fromId` where `toId` sits: dragging upward places the item before
// the target, dragging downward after it, so a drop always swaps positions.
export function reorderIds(ids, fromId, toId) {
  const from = ids.indexOf(fromId);
  const to = ids.indexOf(toId);
  if (from === -1 || to === -1 || from === to) return ids;
  const next = ids.filter((id) => id !== fromId);
  next.splice(next.indexOf(toId) + (to < from ? 0 : 1), 0, fromId);
  return next;
}

export const SessionSidebar = memo(function SessionSidebar({
  collapsed,
  settings,
  projects,
  sessionGroups,
  automations = [],
  onToggleAutomation,
  activeScopeId,
  activeSessionId,
  onCreateSession,
  onDeleteSession,
  onForkSession,
  onDeleteProject,
  onSelectSession,
  onReorderProjects,
  onReorderSessions,
  onToggleSessionPin,
  onChooseProject,
  onOpenSettings,
  onOpenScheduled,
  scheduledActive,
  onToggle,
}) {
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [expandedGroups, setExpandedGroups] = useState({});
  const [dragItem, setDragItem] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const [query, setQuery] = useState("");
  const [recentExpanded, setRecentExpanded] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const search = query.trim().toLocaleLowerCase();
  // Searching rebuilds the filtered tree; without a query reuse the poll
  // payload identity so memoization below can actually skip work.
  const filteredGroups = search
    ? sessionGroups
        .map((group) => ({ ...group, sessions: (group.sessions || []).filter((session) => `${session.title || ""} ${session.session_id || ""} ${group.scope?.label || ""}`.toLocaleLowerCase().includes(search)) }))
        .filter((group) => group.sessions.length)
    : sessionGroups;
  const recentPool = useMemo(
    () => buildRecentConversations(sessionGroups, search),
    [sessionGroups, search],
  );
  const recentLimit = recentExpanded ? RECENT_MAX_SESSIONS : RECENT_VISIBLE_SESSIONS;
  const visibleRecent = recentPool.slice(0, recentLimit);
  const [acknowledgedStatuses, setAcknowledgedStatuses] = useState(loadAcknowledgedSessionStatuses);
  const [badgeBaseline, setBadgeBaseline] = useState(loadBadgeBaseline);
  const [badgeReady, setBadgeReady] = useState(false);

  function acknowledgeSessionStatus(session) {
    setAcknowledgedStatuses((current) => acknowledgeStoredSessionStatus(current, session));
  }

  // The taskbar badge mirrors the sidebar dots but only for completions from
  // now on: the first poll snapshot seeds every already-finished session into
  // the baseline, so the historical backlog never lights up the badge.
  useEffect(() => {
    if (badgeReady || sessionGroups.length === 0) {
      return;
    }
    setBadgeBaseline(seedBadgeBaseline(sessionGroups));
    setBadgeReady(true);
  }, [badgeReady, sessionGroups]);

  // The session the user is looking at is its own acknowledgement: watching a
  // run finish in the open session does not need a taskbar nag.
  useEffect(() => {
    if (!activeSessionId) {
      return;
    }
    for (const group of sessionGroups) {
      const session = (group?.sessions || []).find((item) => item?.session_id === activeSessionId);
      if (!session) {
        continue;
      }
      if (isUnacknowledgedCompletedSession(session, acknowledgedStatuses)) {
        acknowledgeSessionStatus(session);
      }
      break;
    }
  }, [activeSessionId, sessionGroups, acknowledgedStatuses]);

  const unacknowledgedCompletedCount = useMemo(
    () => (badgeReady
      ? countUnacknowledgedCompletedSessions(sessionGroups, acknowledgedStatuses, badgeBaseline)
      : 0),
    [badgeReady, sessionGroups, acknowledgedStatuses, badgeBaseline],
  );
  useEffect(() => {
    updateTaskbarBadge(unacknowledgedCompletedCount);
  }, [unacknowledgedCompletedCount]);

  function toggleGroup(scopeId) {
    setCollapsedGroups((current) => ({ ...current, [scopeId]: !current[scopeId] }));
  }

  function endDrag() {
    setDragItem(null);
    setDropTarget(null);
  }

  function beginDrag(event, item) {
    if (search) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", JSON.stringify(item));
    setDragItem(item);
  }

  function dragOverProject(event, scopeId) {
    if (!dragItem || dragItem.type !== "project" || dragItem.scopeId === scopeId || scopeId === "conversation") return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropTarget({ type: "project", scopeId });
  }

  function dropOnProject(event, scopeId) {
    if (!dragItem || dragItem.type !== "project" || dragItem.scopeId === scopeId) return;
    event.preventDefault();
    const scopeIds = sessionGroups.map((group) => group.scope?.scope_id).filter(Boolean);
    onReorderProjects(reorderIds(scopeIds, dragItem.scopeId, scopeId));
    endDrag();
  }

  function dragOverSession(event, scopeId, sessionId) {
    if (!dragItem || dragItem.type !== "session" || dragItem.scopeId !== scopeId || dragItem.sessionId === sessionId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropTarget({ type: "session", scopeId, sessionId });
  }

  function dropOnSession(event, scope, sessions, sessionId) {
    if (!dragItem || dragItem.type !== "session" || dragItem.scopeId !== scope.scope_id || dragItem.sessionId === sessionId) return;
    event.preventDefault();
    onReorderSessions(
      scope.scope_id,
      reorderIds(sessions.map((item) => item.session_id), dragItem.sessionId, sessionId),
    );
    endDrag();
  }

  function acknowledgeSessionStatus(session) {
    setAcknowledgedStatuses((current) => acknowledgeStoredSessionStatus(current, session));
  }

  return (
    <aside className="history" aria-label="Session history">
      <div className="history-shell">
        <div className="history-top">
          <div className="brand">
            <img className="brand-spark" src={laraLogo} alt="" aria-hidden="true" />
            <h1 className="brand-title">{collapsed ? "L" : "Lara"}</h1>
          </div>
          <div className="history-header-actions">
            <button className="icon-button header-action" title="Settings" aria-label="Settings" type="button" onClick={onOpenSettings}>
              <Settings2 aria-hidden="true" />
            </button>
            <button
              className="icon-button rail-button"
              title={collapsed ? "Expand history" : "Collapse history"}
              type="button"
              onClick={onToggle}
            >
              {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
            </button>
          </div>
        </div>

        {!collapsed && <>
          <div className="section-label recent-label">最近对话</div>
          <div className="recent-list">
            {recentPool.length === 0 && <div className="empty-state compact">暂无会话</div>}
            {visibleRecent.map(({ session, scope }) => (
              <SessionRow
                active={session.session_id === activeSessionId}
                key={session.session_id}
                scope={scope}
                session={session}
                statusAcknowledged={acknowledgedStatuses[session.session_id] === sessionStatusIdentity(session)}
                onAcknowledgeStatus={acknowledgeSessionStatus}
                onSelectSession={onSelectSession}
                onTogglePin={onToggleSessionPin}
                recent
              />
            ))}
            {recentPool.length > RECENT_VISIBLE_SESSIONS && (
              <button className="session-more" type="button" onClick={() => setRecentExpanded((current) => !current)}>
                {recentExpanded ? "折叠更多会话" : "展开显示更多"}
              </button>
            )}
          </div>
          <button className={scheduledActive ? "section-label scheduled-label active" : "section-label scheduled-label"} type="button" onClick={onOpenScheduled}>定时任务</button>
          {automations.length > 0 && (
            <div className="scheduled-list">
              {automations.map((item) => (
                <div
                  className="scheduled-row"
                  key={item.automation_id}
                  role="button"
                  tabIndex={0}
                  title={item.prompt || item.name}
                  onClick={() => onOpenScheduled()}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpenScheduled();
                    }
                  }}
                >
                  <span className="scheduled-name">{item.name}</span>
                  {item.status === "active" ? (
                    <button className="scheduled-action" type="button" title="暂停"
                      aria-label={`暂停 ${item.name}`}
                      onClick={(event) => { event.stopPropagation(); onToggleAutomation(item); }}>
                      <Pause aria-hidden="true" />
                    </button>
                  ) : item.status === "paused" ? (
                    <button className="scheduled-action" type="button" title="恢复"
                      aria-label={`恢复 ${item.name}`}
                      onClick={(event) => { event.stopPropagation(); onToggleAutomation(item); }}>
                      <Play aria-hidden="true" />
                    </button>
                  ) : (
                    <span className="scheduled-status" aria-hidden="true"><Check /></span>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="section-label projects-label">
            <span>项目</span>
            <span className="section-label-actions">
              <button
                className={searchOpen ? "icon-button section-action active" : "icon-button section-action"}
                title="搜索会话"
                aria-label="搜索会话"
                aria-expanded={searchOpen}
                type="button"
                onClick={() => {
                  if (searchOpen && query) {
                    setQuery("");
                    return;
                  }
                  setSearchOpen((current) => !current);
                }}
              ><Search aria-hidden="true" /></button>
              <button className="icon-button section-action" title="Open project" aria-label="Open project"
                aria-haspopup="dialog" type="button" onClick={onChooseProject}><FolderPlus aria-hidden="true" /></button>
            </span>
          </div>
          {searchOpen && (
            <label className="session-search">
              <Search size={16} aria-hidden="true" />
              <input
                autoFocus
                aria-label="搜索任务"
                placeholder="搜索任务或项目"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onBlur={() => { if (!query) setSearchOpen(false); }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    if (query) setQuery("");
                    else setSearchOpen(false);
                  }
                }}
              />
            </label>
          )}
        </>}
        {collapsed && <button className={scheduledActive ? "icon-button scheduled-rail active" : "icon-button scheduled-rail"} title="定时任务" type="button" onClick={onOpenScheduled}><CalendarClock aria-hidden="true" /></button>}
        <div className="session-groups">
          {sessionGroups.length === 0 && <div className="empty-state">No chats yet</div>}
          {search && filteredGroups.length === 0 && <div className="empty-state">没有匹配的任务，试试其他关键词。</div>}
          {filteredGroups.map((group) => {
            const scope = group.scope || {};
            const isCollapsed = !search && (collapsedGroups[scope.scope_id] ?? group.collapsed);
            const isCurrentProject = scope.scope_id === scopeIdFromWorkspace(settings.workspace_root);
            const canSwitchBeforeRemoving = !scope.workspace_root || projects.some(
              (project) => project.workspace_root
                && projectPathKey(project.workspace_root) !== projectPathKey(scope.workspace_root),
            );
            const groupSessions = group.sessions || [];
            const showAllSessions = Boolean(expandedGroups[scope.scope_id]);
            const visibleSessions = search || showAllSessions
              ? groupSessions
              : groupSessions.slice(0, MAX_VISIBLE_SESSIONS);
            return (
              <section
                className={`session-group${dropTarget?.type === "project" && dropTarget.scopeId === scope.scope_id ? " drop-target" : ""}`}
                key={scope.scope_id || scope.label}
                onDragOver={(event) => dragOverProject(event, scope.scope_id)}
                onDrop={(event) => dropOnProject(event, scope.scope_id)}
              >
                <button
                  className={`${scope.scope_id === activeScopeId ? "session-group-header active" : "session-group-header"} has-new-chat${scope.workspace_root ? " has-project-actions" : ""}`}
                  title={scope.tooltip || scope.label}
                  type="button"
                  draggable={Boolean(scope.workspace_root) && !search}
                  onDragStart={(event) => beginDrag(event, { type: "project", scopeId: scope.scope_id })}
                  onDragEnd={endDrag}
                  onClick={() => toggleGroup(scope.scope_id)}
                >
                  <span className="group-label">{scope.label || "Workspace"}</span>
                </button>
                <div className="session-group-actions">
                  <button className="session-group-new-chat" type="button"
                      aria-label={`New chat in ${scope.label || "project"}`}
                      title={`New chat in ${scope.label || "project"}`}
                      onClick={() => onCreateSession(scope)}>
                    <Plus aria-hidden="true" />
                  </button>
                  {scope.workspace_root && (
                    <button className="session-group-delete" type="button"
                        disabled={isCurrentProject && !canSwitchBeforeRemoving}
                        aria-label={`Remove project ${scope.label || "project"}`}
                        title={isCurrentProject && !canSwitchBeforeRemoving ? "Open another project before removing this one" : "Remove project from sidebar"}
                        onClick={() => onDeleteProject(scope)}>
                      <Trash2 aria-hidden="true" />
                    </button>
                  )}
                  <button
                    className="session-group-chevron"
                    type="button"
                    aria-label={isCollapsed ? `Expand ${scope.label || "project"}` : `Collapse ${scope.label || "project"}`}
                    title={isCollapsed ? "展开" : "折叠"}
                    onClick={() => toggleGroup(scope.scope_id)}
                  >
                    {isCollapsed ? <ChevronDown aria-hidden="true" /> : <ChevronUp aria-hidden="true" />}
                  </button>
                </div>
                {!isCollapsed && (
                  <div className="session-list">
                    {groupSessions.length === 0 && <div className="empty-state compact">No chats yet</div>}
                    {visibleSessions.map((session) => (
                        <SessionRow
                          active={session.session_id === activeSessionId}
                          key={session.session_id}
                          scope={scope}
                          session={session}
                        statusAcknowledged={acknowledgedStatuses[session.session_id] === sessionStatusIdentity(session)}
                        onAcknowledgeStatus={acknowledgeSessionStatus}
                        onDeleteSession={onDeleteSession}
                        onForkSession={onForkSession}
                        onSelectSession={onSelectSession}
                        dragEnabled={!search}
                        dropActive={dropTarget?.type === "session" && dropTarget.sessionId === session.session_id}
                        onDragStart={(event) => beginDrag(event, { type: "session", scopeId: scope.scope_id, sessionId: session.session_id })}
                        onDragOver={(event) => dragOverSession(event, scope.scope_id, session.session_id)}
                        onDrop={(event) => dropOnSession(event, scope, groupSessions, session.session_id)}
                        onDragEnd={endDrag}
                      />
                    ))}
                    {!search && groupSessions.length > MAX_VISIBLE_SESSIONS && (
                      <button
                        className="session-more"
                        type="button"
                        onClick={() => setExpandedGroups((current) => ({ ...current, [scope.scope_id]: !current[scope.scope_id] }))}
                      >
                        {showAllSessions ? "收起" : `显示全部 ${groupSessions.length} 个会话`}
                      </button>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </div>
    </aside>
  );
});

function SessionRow({
  active,
  scope,
  session,
  statusAcknowledged,
  onAcknowledgeStatus,
  onDeleteSession,
  onForkSession,
  onSelectSession,
  onTogglePin,
  recent = false,
  dragEnabled = false,
  dropActive = false,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}) {
  const title = cleanSessionTitle(session.title) || session.session_id || "Untitled chat";
  const statusKind = sessionStatusKind(session.last_case_run_status);
  const showStatus = statusKind === "running" || !statusAcknowledged;
  const canDelete = statusKind !== "running";
  const canFork = statusKind !== "running";
  const pinned = Boolean(session.pinned);
  const titleRef = useRef(null);
  const [titleShift, setTitleShift] = useState(0);

  // 悬停跑马灯需要知道标题超出悬停宽度的距离；悬停宽度是标题列的 80%，
  // 与 CSS 里 .session-row:hover .session-title 的 max-width 保持一致。
  useEffect(() => {
    const node = titleRef.current;
    const copy = node?.parentElement;
    if (!node || !copy) {
      return undefined;
    }
    const measure = () => {
      setTitleShift(Math.max(0, node.scrollWidth - Math.round(copy.clientWidth * 0.8)));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(copy);
    return () => observer.disconnect();
  }, [title]);

  function selectSession() {
    onAcknowledgeStatus(session);
    onSelectSession(session, scope);
  }

  function togglePin(event) {
    event.stopPropagation();
    onTogglePin?.(session, scope, !pinned);
  }

  return (
    <div
      className={`session-row${recent ? " recent-row" : ""}${active ? " active" : ""}${dropActive ? " drop-target" : ""}${showStatus ? " has-status" : ""}`}
      role="button"
      tabIndex={0}
      draggable={dragEnabled}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      onClick={selectSession}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectSession();
        }
      }}
    >
      <div className="session-copy">
        <div className="session-title" title={title} ref={titleRef} style={{ "--title-shift": `${titleShift}px` }}>
          <span className="session-title-text">{title}</span>
        </div>
      </div>
      <div className="session-row-actions">
        {showStatus && <span className={`session-indicator ${statusKind}`} aria-label={sessionStatusLabel(statusKind)} />}
        {onTogglePin && (
          <button
            className={pinned ? "session-pin pinned" : "session-pin"}
            title={pinned ? "取消固定" : "固定到最近对话"}
            aria-label={pinned ? `Unpin ${title}` : `Pin ${title}`}
            aria-pressed={pinned}
            type="button"
            onClick={togglePin}
          >
            <Pin aria-hidden="true" />
          </button>
        )}
        {canFork && onForkSession && (
          <button
            className="session-fork"
            title="Fork session"
            aria-label={`Fork ${title}`}
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onForkSession(session.session_id, scope);
            }}
          >
            <Copy aria-hidden="true" />
          </button>
        )}
        {canDelete && onDeleteSession && (
          <button
            className="session-delete"
            title="Delete session"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onDeleteSession(session.session_id, scope);
            }}
          >
            <Trash2 aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}

export const NO_ENTERING_MESSAGE_IDS = new Set();

// Which message ids should play the entrance animation: only a strict
// append to the previously rendered transcript of the same session view.
// History reloads and session switches replace every id wholesale, and
// replaying the reveal for the whole transcript reads as a flash.
export function computeEnteringMessageIds(previous, sessionId, messages) {
  if (previous.sessionId !== sessionId || messages.length <= previous.ids.length) {
    return NO_ENTERING_MESSAGE_IDS;
  }
  for (let index = 0; index < previous.ids.length; index += 1) {
    if (messages[index]?.id !== previous.ids[index]) {
      return NO_ENTERING_MESSAGE_IDS;
    }
  }
  return new Set(messages.slice(previous.ids.length).map((message) => message.id));
}

export function ChatPane({ activeSession, messages, activityCollapseToken, settings, status, running, steeringReady = false, approvals, pendingSteerings = [], api, onSendMessage, onForkSession, onSteering, onCancelTurn, onApproval, projects = [], onSelectProject, onChooseProject, onChangePermissions, pendingNewModelGroup = "", onChangeNewModelGroup = () => {}, pendingNewSessionScope = "", onChangeModel = () => {}, composerDrafts = new Map() }) {
  const empty = messages.length === 0;
  const selectedScope = activeSession?.scope_id || pendingNewSessionScope || scopeIdFromWorkspace(settings.workspace_root);
  const composerDraftKey = activeSession ? activeSession.session_id : `new-chat:${selectedScope}`;
  // ChatPane remounts on every session switch, so the App-level composerDrafts
  // map is the only copy of half-typed text; re-read it on mount.
  const [draft, setDraft] = useState(() => composerDrafts.get(composerDraftKey) || "");
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [configuring, setConfiguring] = useState(false);
  const [configError, setConfigError] = useState("");
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const sendingRef = useRef(false);
  const pendingSteeringRef = useRef(null);
  // The active session is already on its own workspace, so the fork passes
  // only the scope id; the App handler skips its project-switch branch.
  const handleForkAtMessage = useCallback(
    (atSequence) => {
      if (activeSession && onForkSession) {
        onForkSession(activeSession.session_id, { scope_id: selectedScope }, atSequence);
      }
    },
    [activeSession, onForkSession, selectedScope],
  );
  async function configure(action) {
    setConfiguring(true);
    setConfigError("");
    try { await action(); } catch (err) { setConfigError(readableError(err)); }
    finally { setConfiguring(false); }
  }
  function updateDraft(value) {
    setDraft(value);
    syncComposerDraft(composerDrafts, composerDraftKey, value);
  }
  const transcriptRef = useRef(null);
  const composerRef = useRef(null);
  const fileInputRef = useRef(null);
  const followTranscriptRef = useRef(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  // Scroll events fire for programmatic pins and scroll-anchoring adjustments
  // too, and during streaming either can measure a large distance-to-bottom
  // without the user touching anything; treating that as "user scrolled away"
  // was silently ending the auto-follow. Re-evaluate following only after
  // real user input (wheel, touch, scrollbar) marks the user as driving.
  const transcriptUserScrollingRef = useRef(false);
  // Only an explicit "回到最新" click animates. Session entry and stream
  // follow must land at the bottom without a visible scroll-through of the
  // transcript, and re-entering a long session must not reveal its top.
  const pinTranscriptToLatest = useCallback((smooth = false) => {
    const node = transcriptRef.current;
    if (!node) return;
    followTranscriptRef.current = true;
    scrollTranscriptToLatest(node, smooth);
    transcriptUserScrollingRef.current = false;
    setAwayFromLatest(false);
  }, []);
  const [visibleMessageLimit, setVisibleMessageLimit] = useState(INITIAL_TRANSCRIPT_MESSAGE_LIMIT);
  const chatTitle = activeSession?.title || "选择或创建会话";
  const visibleMessages = useMemo(
    () => latestItems(messages, visibleMessageLimit),
    [messages, visibleMessageLimit],
  );
  const hiddenMessageCount = Math.max(0, messages.length - visibleMessages.length);

  // The entrance animation plays only for messages appended to the session
  // view the user is already looking at; see enteringMessageIds.
  const messageEntryRef = useRef({ sessionId: undefined, ids: [] });
  const enteringMessageIds = useMemo(
    () => computeEnteringMessageIds(messageEntryRef.current, activeSession?.session_id, messages),
    [messages, activeSession?.session_id],
  );
  useEffect(() => {
    messageEntryRef.current = {
      sessionId: activeSession?.session_id,
      ids: messages.map((message) => message.id),
    };
  });

  useEffect(() => {
    if (followTranscriptRef.current) pinTranscriptToLatest();
  }, [messages, activityCollapseToken]);
  // Content can also grow after that scroll effect measured it: tool groups
  // expanding from their own effect land a render later, image attachments
  // load without intrinsic size, and panel resizes change the scrollport.
  // While the user is following, keep the transcript pinned through all of
  // them; the scrollport itself never fires the observer for scrollTop
  // changes, so this cannot loop.
  const transcriptResizeRef = useRef(null);
  useEffect(() => {
    const node = transcriptRef.current;
    if (!node || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver(() => {
      if (followTranscriptRef.current) {
        pinTranscriptToLatest();
      }
    });
    observer.observe(node);
    transcriptResizeRef.current = observer;
    return () => {
      transcriptResizeRef.current = null;
      observer.disconnect();
    };
  }, []);
  useEffect(() => {
    const node = transcriptRef.current;
    const observer = transcriptResizeRef.current;
    if (!node || !observer) {
      return;
    }
    for (const child of node.children) {
      observer.observe(child);
    }
  }, [visibleMessages, activityCollapseToken]);
  // Layout phase: the pin lands before the first paint, so re-entering a
  // long session never shows the top of the transcript or a scroll-through.
  useLayoutEffect(() => {
    followTranscriptRef.current = true;
    setAwayFromLatest(false);
    setVisibleMessageLimit(INITIAL_TRANSCRIPT_MESSAGE_LIMIT);
    pinTranscriptToLatest();
  }, [activeSession?.session_id]);
  useEffect(() => {
    const input = composerRef.current;
    if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; }
  }, [draft]);

  async function submit() {
    const text = draft.trim();
    const attachments = pendingAttachments;
    if ((!text && !attachments.length) || sendingRef.current || configuring) {
      return;
    }
    setConfigError("");
    if (running) {
      if (!steeringReady) return;
      sendingRef.current = true;
      setSending(true);
      // The input id also keys the runtime's idempotency window, so a failed
      // submit retries the same input; a cleared composer starts a new one.
      if (pendingSteeringRef.current?.text !== text || pendingSteeringRef.current?.attachments !== attachments) {
        pendingSteeringRef.current = { text, inputId: crypto.randomUUID() };
      }
      try {
        await onSteering(text, pendingSteeringRef.current.inputId, attachments);
        updateDraft("");
        setPendingAttachments([]);
        pendingSteeringRef.current = null;
        followTranscriptRef.current = true;
      } catch (err) {
        setConfigError(readableError(err));
      } finally {
        sendingRef.current = false;
        setSending(false);
      }
      return;
    }
    updateDraft("");
    setPendingAttachments([]);
    followTranscriptRef.current = true;
    onSendMessage(text, null, attachments);
  }

  async function pickFiles(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    const paths = files.map((file) => {
      // Electron 32+ removed File.path; the preload bridges webUtils for it.
      const path = window.laraDesktop?.pathForFile?.(file) || file.path || file.name;
      return path;
    });
    setPendingAttachments((items) => {
      const next = [...items];
      for (const path of paths) {
        if (!next.includes(path) && next.length < 10) next.push(path);
      }
      return next;
    });
    composerRef.current?.focus();
  }

  // Clipboard and drag-drop files have no workspace path, so their bytes go
  // through the upload API and land inside the workspace's .lara/uploads.
  async function uploadFiles(files) {
    if (sendingRef.current || configuring) return;
    const room = 10 - pendingAttachments.length;
    if (room <= 0) {
      setConfigError("附件数量已达上限（10 个）");
      return;
    }
    const accepted = files.slice(0, room);
    setConfigError("");
    setUploadingCount((count) => count + accepted.length);
    const uploaded = [];
    try {
      for (const file of accepted) {
        uploaded.push(await api.uploadAttachment({ file, scopeId: selectedScope }));
      }
    } catch (err) {
      setConfigError(readableError(err));
    } finally {
      setUploadingCount((count) => Math.max(0, count - accepted.length));
    }
    if (uploaded.length) {
      setPendingAttachments((items) => {
        const next = [...items];
        for (const item of uploaded) {
          if (!next.includes(item.path) && next.length < 10) next.push(item.path);
        }
        return next;
      });
      composerRef.current?.focus();
    }
  }

  function handleComposerPaste(event) {
    const files = filesFromDataTransfer(event.clipboardData);
    if (files.length) {
      event.preventDefault();
      void uploadFiles(files);
    }
  }

  // Removing a pending chip also deletes the stored upload, so pasted media
  // does not keep piling up inside the project's .lara/uploads. Files picked
  // from the workspace are only referenced, never deleted here.
  async function removePendingAttachment(path) {
    setPendingAttachments((items) => items.filter((item) => item !== path));
    if (!isUploadedAttachment(path)) return;
    try {
      await api.deleteUpload(selectedScope, path);
    } catch (err) {
      setConfigError(readableError(err));
    }
  }

  function handleComposerDrop(event) {
    setDragOver(false);
    const files = filesFromDataTransfer(event.dataTransfer);
    if (files.length) {
      event.preventDefault();
      void uploadFiles(files);
    }
  }

  return (
    <section className={`chat${empty ? " chat-empty" : ""}`} aria-label="对话">
      <header className="chat-header">
        <div className="chat-title">
          <h2 title={chatTitle}>{chatTitle}</h2>
          <p>
            <span title={settings.workspace_root}>{activeSession?.scope_id === "conversation" ? "独立对话" : shortPath(settings.workspace_root) || "未选择项目"}</span>
            <span aria-hidden="true"> · </span>{activeSession?.model_group_name || settings.default_model_group || "未配置组"}
            <span aria-hidden="true"> / </span>{activeSession?.selected_model_key || primaryModel(settings)}
          </p>
        </div>
        {/* Remounts on status change so the entrance animation marks each transition. */}
        <div key={status} className={`status-pill ${statusTone(status)}`}>{statusLabel(status)}</div>
      </header>

      <div className="transcript" ref={transcriptRef} onScroll={(event) => {
        if (!transcriptUserScrollingRef.current) return;
        const node = event.currentTarget;
        followTranscriptRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
        setAwayFromLatest(!followTranscriptRef.current);
      }} onWheel={() => { transcriptUserScrollingRef.current = true; }} onTouchStart={() => { transcriptUserScrollingRef.current = true; }} onPointerDown={(event) => {
        // Scrollbar drags target the scrollport itself; clicks on message
        // content bubble from children and must not end the auto-follow.
        if (event.target === event.currentTarget) transcriptUserScrollingRef.current = true;
      }}>
        {hiddenMessageCount > 0 && (
          <button
            className="load-earlier-messages"
            type="button"
            onClick={() => setVisibleMessageLimit((value) => value + INITIAL_TRANSCRIPT_MESSAGE_LIMIT)}
          >
            显示更早消息（还有 {hiddenMessageCount} 条）
          </button>
        )}
        {visibleMessages.map((message) => (
          <MessageRow key={message.id} message={message} entering={enteringMessageIds.has(message.id)} activityCollapseToken={activityCollapseToken} api={api} scopeId={selectedScope} onForkAtMessage={handleForkAtMessage} />
        ))}
        {pendingSteerings.map((item) => (
          <article className="message user message-enter pending-steering" key={`pending-${item.inputId}`}>
            <div className="bubble">
              <span className="pending-steering-label">{item.stale ? "未生效 · 本轮已结束" : "待生效 · 即将打断当前步骤"}</span>
              {item.text}
              {Array.isArray(item.attachments) && item.attachments.length > 0 && (
                <span className="message-attachments">
                  {item.attachments.map((path) => (
                    <AttachmentPreview key={path} api={api} scopeId={selectedScope} path={path} variant="message" />
                  ))}
                </span>
              )}
            </div>
          </article>
        ))}
      </div>

      {approvals.map((approval) => (
        <aside className="approval-card" key={approval.approval_id} aria-live="assertive">
          <div>
            <span className="approval-kicker">执行已暂停</span>
            <strong>{approval.tool_name}</strong>
            <code>{safeJsonStringify(approval.arguments || {})}</code>
          </div>
          <div className="approval-actions">
            <button className="plain-action" type="button" onClick={() => onApproval(approval, false)}>拒绝</button>
            <button className="send" type="button" onClick={() => onApproval(approval, true)}>批准一次</button>
          </div>
        </aside>
      ))}

      <footer className="composer">
        {messages.length === 0 && (
          <div className="welcome">
            <span className="welcome-eyebrow">LARA WORKSPACE</span>
            <h3>从一个任务开始</h3>
            <p>描述你的目标，Lara 会结合当前项目分析、执行并整理结果。</p>
            <div className="starter-actions">{["介绍这个项目的结构", "检查当前代码中的问题", "梳理待完成的工作"].map((prompt) => <button key={prompt} type="button" onClick={() => { updateDraft(prompt); composerRef.current?.focus(); }}>{prompt}<ArrowUp size={16} aria-hidden="true" /></button>)}</div>
          </div>
        )}

        {awayFromLatest && <button className="jump-latest" type="button" onClick={() => pinTranscriptToLatest(true)}><ArrowDown size={14} />回到最新</button>}
        <div className="composer-surface">
        <div className={`composer-box${dragOver ? " drag-over" : ""}`}
          onDragOver={(event) => { if (dataTransferHasFiles(event.dataTransfer)) { event.preventDefault(); setDragOver(true); } }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragOver(false); }}
          onDrop={handleComposerDrop}
        >
          {pendingAttachments.length > 0 && (
            <div className="attachment-chips">
              {pendingAttachments.map((path) => (
                <AttachmentChip key={path} path={path} scopeId={selectedScope} api={api} onRemove={() => void removePendingAttachment(path)} />
              ))}
            </div>
          )}
          <textarea
            ref={composerRef}
            aria-label="任务内容"
            disabled={sending || configuring}
            placeholder={running ? (steeringReady ? "追加指令，调整当前任务方向…" : "正在连接执行，可以先输入指令…") : "描述任务，或继续追问…"}
            value={draft}
            onChange={(event) => updateDraft(event.target.value)}
            onPaste={handleComposerPaste}
            onKeyDown={(event) => {
              if (shouldSubmitComposer(event)) {
                event.preventDefault();
                submit();
              }
            }}
          />
        </div>
        <div className="composer-context">
          {!empty ? <span className="composer-option" title={selectedScope === "conversation" ? "独立对话" : selectedScope.slice(8)}>
            <FolderCode size={14} aria-hidden="true" />
            {selectedScope === "conversation" ? "独立对话" : projects.find((project) => scopeIdFromWorkspace(project.workspace_root) === selectedScope)?.label || shortPath(selectedScope.slice(8))}
          </span> : <ComposerMenu label="任务项目" icon={<FolderCode size={14} aria-hidden="true" />} value={selectedScope} disabled={running || configuring} onChange={(value) => {
              if (value === "browse") void configure(() => onChooseProject());
              else void configure(() => onSelectProject(value === "conversation" ? { scope_id: "conversation" } : { scope_id: value, workspace_root: value.slice(8) }));
            }} options={[
              { value:"conversation", label:"独立对话" },
              ...(settings.workspace_root && !projects.some((project) => projectPathKey(project.workspace_root) === projectPathKey(settings.workspace_root)) ? [{ value:scopeIdFromWorkspace(settings.workspace_root), label:shortPath(settings.workspace_root), description:settings.workspace_root }] : []),
              ...projects.map((project) => ({ value:scopeIdFromWorkspace(project.workspace_root), label:project.label || shortPath(project.workspace_root), description:project.workspace_root })),
              { value:"browse", label:"选择其他文件夹…", action:true },
            ]} />}
          <ComposerMenu label="任务权限（全局）" title="全局权限设置，对后续新运行生效" value={settings.approvals_enabled === false ? "full" : "ask"} disabled={running || configuring} onChange={(value) => void configure(() => onChangePermissions(value === "ask"))}
            options={[{ value:"ask", label:"逐次审批" }, { value:"full", label:"完全访问" }]} />
          {!activeSession && <ComposerMenu label="模型组" title="模型组在对话创建后固定" value={pendingNewModelGroup || settings.default_model_group || ""} disabled={running || configuring} onChange={onChangeNewModelGroup}
            options={Object.keys(settings.model_groups || {}).map((name) => ({ value: name, label: name, description: (settings.model_groups[name]?.models || []).join(" → ") }))} />}
          {activeSession && <ComposerMenu label="首选模型" title={`固定模型组：${activeSession.model_group_name || "未配置"}`} value={activeSession.selected_model_key || ""} disabled={running || configuring} onChange={(value) => void configure(() => onChangeModel(value))}
            options={(activeSession.selectable_models || []).map((model) => ({ value: model.model_key, label: model.model_key, description: modelCapabilitySummary(model, settings) }))} />}
          <div className="composer-send-actions">
            <input ref={fileInputRef} type="file" multiple hidden aria-hidden="true" tabIndex={-1} onChange={(event) => void pickFiles(event)} />
            <button className="attach" aria-label="添加附件" title="添加工作区文件作为附件（图片/视频原生识别，其他文件转 XML）" disabled={sending || configuring || pendingAttachments.length >= 10} type="button" onClick={() => fileInputRef.current?.click()}>
              <Paperclip aria-hidden="true" />
            </button>
            <span className="composer-hint">{uploadingCount > 0 ? `正在上传附件…（${uploadingCount}）` : running ? (steeringReady ? "Enter 追加指令 · 立即打断当前步骤" : "正在连接执行，连接后可追加指令") : "Enter 发送 · Shift + Enter 换行 · 可粘贴或拖入文件"}</span>
            {running && onCancelTurn && <button className="stop" aria-label="取消当前任务" title="取消当前任务" disabled={cancelling} type="button" onClick={() => {
              if (cancelling) return;
              setCancelling(true);
              Promise.resolve(onCancelTurn()).catch((err) => setConfigError(readableError(err))).finally(() => setCancelling(false));
            }}>
              <Square aria-hidden="true" />
            </button>}
            <button className="send" aria-label={running ? (steeringReady ? "追加指令" : "正在连接") : "发送"} title={running ? (steeringReady ? "追加指令" : "正在连接") : "发送"} disabled={(running && !steeringReady) || sending || configuring || uploadingCount > 0 || (!draft.trim() && !pendingAttachments.length)} type="button" onClick={submit}>
              <ArrowUp aria-hidden="true" />
            </button>
          </div>
        </div>
        {configError && <p className="composer-config-error" role="alert">{configError}</p>}
        </div>
      </footer>
    </section>
  );
}

function ComposerMenu({ label, title, icon, value, options, disabled, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    const items = menuRef.current?.querySelectorAll('[role^="menuitem"]');
    const selected = options.findIndex((option) => option.value === value);
    items?.[Math.max(0, selected)]?.focus();
    const dismiss = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  return <div className="composer-menu" ref={rootRef} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button ref={triggerRef} className="composer-menu-trigger" type="button" title={title} aria-label={label} aria-haspopup="menu" aria-expanded={open} disabled={disabled}
      onClick={() => setOpen((current) => !current)} onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); }
      }}>
      {icon}<span>{options.find((option) => option.value === value)?.label || label}</span><ChevronRight size={12} className="composer-menu-chevron" aria-hidden="true" />
    </button>
    {open && <div ref={menuRef} className="composer-menu-popup" role="menu" aria-label={label} onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); triggerRef.current?.focus(); }
      const items = Array.from(menuRef.current.querySelectorAll('[role^="menuitem"]'));
      const index = items.indexOf(document.activeElement);
      const target = event.key === "ArrowDown" ? (index + 1) % items.length : event.key === "ArrowUp" ? (index - 1 + items.length) % items.length : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : -1;
      if (target >= 0) { event.preventDefault(); items[target]?.focus(); }
    }}>
      <div className="composer-menu-heading">{label}</div>
      {options.map((option) => <button key={option.value} type="button" tabIndex={-1} role={option.action ? "menuitem" : "menuitemradio"} aria-checked={option.action ? undefined : value === option.value}
        className={`composer-menu-item${option.action ? " menu-action" : ""}`} title={option.description}
        onClick={() => { setOpen(false); triggerRef.current?.focus(); if (option.value !== value) onChange(option.value); }}>
        <span>{option.label}{option.description && <small>{option.description}</small>}</span>
        {value === option.value && <Check size={14} aria-hidden="true" />}
        {option.action && <Folder size={14} aria-hidden="true" />}
      </button>)}
    </div>}
  </div>;
}

// One composer attachment pill: type icon or thumbnail, the original file
// name for non-media files, and a remove action that stays inside the capped
// pill width (the name span shrinks with an ellipsis instead of overflowing).
export function AttachmentChip({ path, scopeId, api, onRemove }) {
  const media = attachmentMediaType(path);
  return (
    <span className={`attachment-chip${media === "file" ? "" : " attachment-chip-media"}`} title={path}>
      <AttachmentPreview api={api} scopeId={scopeId} path={path} variant="chip" />
      {media === "file" && <span className="attachment-chip-name">{attachmentDisplayName(path)}</span>}
      <button type="button" aria-label={`移除附件 ${path}`} onClick={onRemove}>
        <X size={12} aria-hidden="true" />
      </button>
    </span>
  );
}

// Multimedia attachments show as thumbnails (images) or type icons
// (video/audio) instead of file-name text; the stored upload's bytes come
// from /uploads/content. Documents (md/txt/pdf) show a document icon,
// everything else keeps the plain paperclip chip.
// A failed preview (file deleted, scope switched) degrades to the icon chip.
function AttachmentPreview({ api, scopeId, path, variant }) {
  const [failed, setFailed] = useState(false);
  const mediaType = attachmentMediaType(path);
  const name = attachmentFileName(path);
  if (mediaType === "image" && !failed) {
    return (
      <img
        className={`attachment-thumb attachment-thumb-${variant}`}
        src={api.uploadContentUrl(scopeId, path)}
        alt={name}
        title={path}
        onError={() => setFailed(true)}
      />
    );
  }
  const Icon = mediaType === "video" ? Film : mediaType === "audio" ? Music : isDocumentAttachment(path) ? FileText : Paperclip;
  return (
    <span className={`attachment-icon attachment-icon-${variant}`} title={path} role="img" aria-label={name}>
      <Icon size={variant === "message" ? 14 : 12} aria-hidden="true" />
    </span>
  );
}

// Native media blocks on transcript messages (assistant output today; user
// media renders through its attachment paths instead). Inline blocks carry
// base64 bytes in the message payload, uploads resolve through /uploads,
// and anything else degrades to an icon so the bubble never breaks.
function validMediaBlocks(media) {
  return (Array.isArray(media) ? media : []).filter(
    (block) => block && typeof block === "object" && block.type === "media",
  );
}

function mediaBlockSrc(block, api, scopeId) {
  const source = block?.source || {};
  if (source.kind === "inline" && typeof source.base64_data === "string" && source.base64_data) {
    return `data:${block.mime_type || "application/octet-stream"};base64,${source.base64_data}`;
  }
  if (source.kind === "resource" && typeof source.uri === "string" && source.uri && isUploadedAttachment(source.uri)) {
    return api?.uploadContentUrl ? api.uploadContentUrl(scopeId, source.uri) : "";
  }
  if (source.kind === "url" && typeof source.url === "string" && source.url) {
    return source.url;
  }
  return "";
}

// Interactive image display shared by transcript media blocks and markdown
// images: a small thumbnail, hover ghost buttons for copy-to-clipboard and
// download, and a click-to-zoom lightbox. Clipboard write needs PNG bytes,
// so other formats are re-encoded through a canvas first.
const MEDIA_IMAGE_EXTENSIONS = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp", "image/avif": "avif" };

function mediaImageExtension(src) {
  const match = String(src || "").match(/^data:image\/([a-z+]+)/i);
  return MEDIA_IMAGE_EXTENSIONS[`image/${match?.[1]?.toLowerCase()}`] || "png";
}

async function fetchImageBlob(src) {
  const response = await fetch(src);
  return response.blob();
}

function toPngBlob(blob) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const image = new window.Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      canvas.getContext("2d").drawImage(image, 0, 0);
      URL.revokeObjectURL(url);
      canvas.toBlob((value) => (value ? resolve(value) : reject(new Error("canvas encode failed"))), "image/png");
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("image decode failed"));
    };
    image.src = url;
  });
}

function MediaImageView({ src, alt = "", title, thumbClassName, onError }) {
  const [zoomed, setZoomed] = useState(false);
  const [notice, setNotice] = useState("");
  const noticeTimerRef = useRef(null);
  useEffect(() => () => window.clearTimeout(noticeTimerRef.current), []);
  useEffect(() => {
    if (!zoomed) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setZoomed(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomed]);

  if (!src) return null;

  function flashNotice(text) {
    setNotice(text);
    window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => setNotice(""), 1400);
  }

  async function copyImage() {
    try {
      let blob = await fetchImageBlob(src);
      if (blob.type !== "image/png") {
        blob = await toPngBlob(blob);
      }
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      flashNotice("已复制到剪贴板");
    } catch {
      flashNotice("复制失败");
    }
  }

  function downloadImage() {
    const anchor = document.createElement("a");
    anchor.href = src;
    anchor.download = `lara-image-${Date.now()}.${mediaImageExtension(src)}`;
    anchor.click();
  }

  return (
    <span className="media-image-frame">
      <img
        src={src}
        alt={alt}
        title={title || alt}
        className={thumbClassName}
        onClick={() => setZoomed(true)}
        onError={onError}
      />
      <span className="media-image-actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="media-image-action" title="复制图片" aria-label="复制图片" onClick={() => void copyImage()}>
          <Copy size={13} aria-hidden="true" />
        </button>
        <button type="button" className="media-image-action" title="下载图片" aria-label="下载图片" onClick={downloadImage}>
          <Download size={13} aria-hidden="true" />
        </button>
      </span>
      {notice && <span className="media-image-notice" role="status">{notice}</span>}
      {zoomed && createPortal(
        <div className="media-lightbox" role="dialog" aria-modal="true" aria-label="查看大图" onClick={() => setZoomed(false)}>
          <img src={src} alt={alt} onClick={(event) => event.stopPropagation()} />
          <button
            type="button"
            className="media-image-action media-lightbox-close"
            aria-label="关闭大图"
            onClick={() => setZoomed(false)}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>,
        document.body,
      )}
    </span>
  );
}

function MediaBlockView({ block, api, scopeId }) {
  const [failed, setFailed] = useState(false);
  const name = `${block.media_type || "media"} · ${block.mime_type || "未知类型"}`;
  const src = mediaBlockSrc(block, api, scopeId);
  if (!src || failed) {
    const Icon = block.media_type === "video" ? Film : block.media_type === "audio" ? Music : ImageIcon;
    return (
      <span className="media-fallback" title={name} role="img" aria-label={name}>
        <Icon size={14} aria-hidden="true" />
        <span>媒体无法预览</span>
      </span>
    );
  }
  if (block.media_type === "image") {
    return (
      <MediaImageView
        src={src}
        alt={name}
        title={name}
        thumbClassName="message-media-image"
        onError={() => setFailed(true)}
      />
    );
  }
  if (block.media_type === "video") {
    return <video className="message-media-video" src={src} controls preload="metadata" title={name} onError={() => setFailed(true)} />;
  }
  if (block.media_type === "audio") {
    return <audio className="message-media-audio" src={src} controls preload="metadata" title={name} onError={() => setFailed(true)} />;
  }
  return null;
}

export function MediaContent({ media, api, scopeId }) {
  const blocks = validMediaBlocks(media);
  if (!blocks.length) return null;
  return (
    <span className="message-media">
      {blocks.map((block, index) => (
        <MediaBlockView key={index} block={block} api={api} scopeId={scopeId} />
      ))}
    </span>
  );
}

// Memoized: during streaming every applied delta re-renders the transcript
// once per frame, and only the active assistant message actually changes.
const MessageRow = memo(function MessageRow({ message, entering = false, activityCollapseToken, api, scopeId = "", onForkAtMessage }) {
  if (message.role === "activity") {
    return <ActivityMessage message={message} collapseToken={activityCollapseToken} api={api} />;
  }

  const hasAssistantActivity =
    message.role === "assistant" &&
    ((Array.isArray(message.sections) && message.sections.length > 0) || message.startedAt !== undefined);
  const hasAssistantMedia =
    message.role === "assistant" && validMediaBlocks(message.media).length > 0;
  if (message.role === "assistant" && !message.content && !hasAssistantActivity && !hasAssistantMedia) {
    return null;
  }

  return (
    <article className={`message ${message.role}${entering ? " message-enter" : ""}`}>
      {message.role !== "user" && <div className="avatar">{message.role === "automation" ? <CalendarClock size={15} /> : "L"}</div>}
      <div className="bubble">
        {message.role === "automation" && <span className="automation-message-label">定时任务触发</span>}
        {message.role === "assistant" ? (
          <>
            {hasAssistantActivity && (
              <AssistantActivity message={message} collapseToken={activityCollapseToken} api={api} />
            )}
            {message.content && <MarkdownContent content={message.content} />}
            {hasAssistantMedia && <MediaContent media={message.media} api={api} scopeId={scopeId} />}
          </>
        ) : (
          <div className="bubble-body">
            {message.content}
            {Array.isArray(message.attachments) && message.attachments.length > 0 && (
              <span className="message-attachments">
                {message.attachments.map((path) => (
                  <AttachmentPreview key={path} api={api} scopeId={scopeId} path={path} variant="message" />
                ))}
              </span>
            )}
          </div>
        )}
        <MessageMeta message={message} onForkAtMessage={onForkAtMessage} />
      </div>
    </article>
  );
});

// Model answers embed generated images as markdown data: URIs; the default
// transform strips every data: URL as unsafe, which left broken images in
// the transcript. Image data URIs cannot execute inside <img>, so allow the
// raster types and delegate everything else to the default policy.
const DATA_IMAGE_URL = /^data:image\/(?:png|jpeg|gif|webp|bmp|avif);base64,[A-Za-z0-9+/=]+$/;
function markdownUrlTransform(url) {
  return DATA_IMAGE_URL.test(String(url)) ? url : defaultUrlTransform(url);
}

// Streaming messages re-parse on every content change; micromark is fast
// enough for transcript-sized texts, and the memo keeps non-streaming
// renders from reparsing.
export function MarkdownContent({ content }) {
  const body = useMemo(
    () => (
      <ReactMarkdown components={MARKDOWN_COMPONENTS} remarkPlugins={[remarkGfmNoAutolink]} urlTransform={markdownUrlTransform}>
        {String(content || "")}
      </ReactMarkdown>
    ),
    [content],
  );
  return <div className="markdown-content">{body}</div>;
}

// Visual parity with the previous hand-rolled renderer: capped heading
// levels, the language label on fenced blocks, the table scroll wrapper,
// and links that open in a new tab. react-markdown already escapes raw
// HTML and strips unsafe URL schemes, so agent output stays inert.
const MARKDOWN_COMPONENTS = {
  a: MarkdownLink,
  h1: MarkdownHeading,
  h2: MarkdownHeading,
  h3: MarkdownHeading,
  h4: MarkdownHeading,
  h5: MarkdownHeading,
  h6: MarkdownHeading,
  pre: MarkdownPre,
  table: MarkdownTable,
  img: MarkdownImage,
};

// Model images arrive as markdown data URIs; render through the shared
// thumbnail view (small, hover actions, click to zoom) instead of a raw
// full-width img. Stripped unsafe URLs render as nothing at all.
function MarkdownImage({ src, alt }) {
  if (!src) return null;
  return <MediaImageView src={src} alt={alt || ""} thumbClassName="markdown-image-thumb" />;
}

function MarkdownHeading({ node, children }) {
  const level = Math.min(Number(String(node?.tagName || "h1").slice(1)) || 1, 3);
  const Tag = `h${level}`;
  return <Tag>{children}</Tag>;
}

function MarkdownPre({ children }) {
  const code = Array.isArray(children) ? children[0] : children;
  const className = String(code?.props?.className || "");
  const language = /(?:^|\s)language-([\w+.#-]+)/.exec(className)?.[1] || "";
  const codeText = codeElementText(code?.props?.children).replace(/\n$/, "");
  return (
    <pre className="markdown-code">
      {(language || codeText) && (
        <span className="markdown-code-head">
          {language && <span className="markdown-code-language">{language}</span>}
          <MarkdownCodeCopy text={codeText} />
        </span>
      )}
      {children}
    </pre>
  );
}

// react-markdown hands the fenced body over as nested text nodes; flatten
// them back into the raw code string for the clipboard.
function codeElementText(value) {
  if (value == null || value === true || value === false) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(codeElementText).join("");
  if (typeof value === "object" && value.props) return codeElementText(value.props.children);
  return "";
}

function MarkdownCodeCopy({ text }) {
  const [copied, markCopied] = useCopiedFeedback();
  return (
    <button
      type="button"
      className="markdown-code-copy"
      aria-label={copied ? "代码已复制" : "复制代码"}
      onClick={() => {
        copyContextValue(text);
        markCopied();
      }}
    >
      {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
    </button>
  );
}

// Shared "已复制" flash so both copy buttons revert without leaking timers.
function useCopiedFeedback() {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);
  useEffect(() => () => window.clearTimeout(timerRef.current), []);
  const markCopied = useCallback(() => {
    setCopied(true);
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(false), 1600);
  }, []);
  return [copied, markCopied];
}

function MarkdownTable({ children }) {
  return <div className="markdown-table-wrap"><table>{children}</table></div>;
}

function MarkdownLink({ children, href }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

// Footer under every transcript message: the checkpoint timestamp, the
// turn's aggregated token usage, and a copy button for the raw content.
function MessageMeta({ message, onForkAtMessage }) {
  if (message.role === "assistant" && message.status === "running") {
    return null;
  }
  // History carries the checkpoint timestamp; live turns fall back to the
  // run's own timing so the footer still shows when it finished.
  const time = formatMessageTimestamp(message.createdAt ?? message.endedAt ?? message.startedAt);
  const usage = usageSummaryText(message.usage);
  const content = String(message.content || "");
  const canForkAt = Boolean(
    onForkAtMessage && message.role === "assistant" && message.checkpointSequence,
  );
  if (!time && !usage && !content.trim() && !canForkAt) {
    return null;
  }
  return (
    <div className="message-meta">
      {time && <span className="message-meta-time">{time}</span>}
      {usage && <span className="message-meta-usage" title={usageDetailText(message.usage)}>{usage}</span>}
      {content.trim() && <MessageMetaCopy text={content} />}
      {canForkAt && (
        <button
          type="button"
          className="message-meta-fork"
          title="从此消息分叉新会话"
          aria-label="从此消息分叉新会话"
          onClick={() => onForkAtMessage(message.checkpointSequence)}
        >
          <GitFork size={12} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

function MessageMetaCopy({ text }) {
  const [copied, markCopied] = useCopiedFeedback();
  return (
    <button
      type="button"
      className="message-meta-copy"
      aria-label={copied ? "已复制" : "复制消息"}
      onClick={() => {
        copyContextValue(text);
        markCopied();
      }}
    >
      {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
    </button>
  );
}

// Accepts the backend ISO timestamp or a client-side epoch value; same-day
// messages show only the clock time so the transcript stays compact.
export function formatMessageTimestamp(value, now = Date.now()) {
  const timestamp = typeof value === "number" ? value : Date.parse(String(value || ""));
  if (!Number.isFinite(timestamp)) {
    return "";
  }
  const date = new Date(timestamp);
  const current = new Date(now);
  const pad = (part) => String(part).padStart(2, "0");
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (date.toDateString() === current.toDateString()) {
    return time;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${time}`;
}

// A turn can span several model calls (tools in between); the footer shows
// their summed usage so the number matches what the reply actually cost.
export function summarizeMessageUsage(usages) {
  const totals = {};
  let counted = false;
  for (const usage of usages) {
    if (!usage || typeof usage !== "object") continue;
    for (const [key, value] of Object.entries(usage)) {
      const number = Number(value);
      if (!Number.isFinite(number) || number <= 0) continue;
      totals[key] = (totals[key] || 0) + number;
      counted = true;
    }
  }
  return counted ? totals : undefined;
}

function formatCount(value) {
  return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

// Footer keeps raw token counts out of the way: compact "↑3.1M ↓28k" style
// symbols in the row, full precision moved into the hover tooltip.
function formatCompact(value) {
  const strip = (text) => text.replace(/\.0$/, "");
  if (value >= 1_000_000) return `${strip((value / 1_000_000).toFixed(1))}M`;
  if (value >= 1_000) return `${strip((value / 1_000).toFixed(1))}k`;
  return String(Math.round(value));
}

function usageSummaryText(usage) {
  if (!usage || typeof usage !== "object") return "";
  const parts = [];
  if (usage.input_tokens > 0) parts.push(`↑ ${formatCompact(usage.input_tokens)}`);
  if (usage.output_tokens > 0) parts.push(`↓ ${formatCompact(usage.output_tokens)}`);
  if (!parts.length) {
    return usage.total_tokens > 0 ? formatCompact(usage.total_tokens) : "";
  }
  return parts.join(" ");
}

function usageDetailText(usage) {
  if (!usage || typeof usage !== "object") return "";
  const labels = [
    ["input_tokens", "输入"],
    ["output_tokens", "输出"],
    ["cached_input_tokens", "缓存"],
    ["reasoning_tokens", "推理"],
    ["total_tokens", "总计"],
  ];
  const parts = labels
    .filter(([key]) => Number(usage[key]) > 0)
    .map(([key, label]) => `${label} ${formatCount(usage[key])}`);
  return parts.length ? `${parts.join(" · ")} tokens` : "";
}

function ActivityMessage({ message, collapseToken, api }) {
  return (
    <article className={`message assistant${message.status === "running" ? " is-running" : ""}`}>
      <div className="avatar">L</div>
      <div className="bubble">
        <AssistantActivity message={message} collapseToken={collapseToken} api={api} />
      </div>
    </article>
  );
}

// Owns the per-second clock for the "Processing for Xs" header so the tick
// re-renders only the header instead of the whole activity subtree.
function ActivityHeader({ message }) {
  const isRunning = message.status === "running";
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!isRunning) {
      return undefined;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isRunning]);

  return (
    <span className="activity-title">
      <span>{activityHeaderText(message, now)}</span>
      <ChevronRight className="activity-chevron" aria-hidden="true" />
    </span>
  );
}

export function AssistantActivity({ message, collapseToken, api }) {
  const isRunning = message.status === "running";
  const [expanded, setExpanded] = useState(isRunning);
  const sections = Array.isArray(message.sections) ? message.sections : [];
  const liveStatus = activityLiveStatus(message);
  const showDetail =
    expanded && (sections.length > 0 || liveStatus || (isRunning && !hasVisibleAssistantContent(message)));

  // Only the collapse token folds finished activity: the whole task ended
  // (terminal event) or the session was reloaded from history. A bare status
  // flip — a steering split or a cancelled turn — keeps the transcript
  // expanded until the task actually completes.
  useEffect(() => {
    setExpanded(message.status === "running");
  }, [collapseToken, message.id]);

  return (
    <div className={expanded ? "activity expanded" : "activity"}>
      <button
        className="activity-head"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <ActivityHeader message={message} />
      </button>
      {showDetail && (
        <div className="activity-detail">
          {sections.map((section, index) => {
            if (section.type === "tools") {
              return <ToolGroup section={section} key={section.id || `tools-${index}`} api={api} />;
            }
            if (section.type === "text" && section.title === "Thinking") {
              const content = String(section.content || "");
              if (!content.trim()) {
                return null;
              }
              return (
                <ThinkingActivity
                  key={section.id || `thinking-${index}`}
                  content={content}
                  running={isRunning && !message.content && index === sections.length - 1}
                  closed={isRunning && (Boolean(message.content) || index !== sections.length - 1)}
                />
              );
            }
            return <ActivityTextSection section={section} key={section.id || `text-${index}`} />;
          })}
          {liveStatus && <div className="activity-live-status">{liveStatus}</div>}
        </div>
      )}
    </div>
  );
}

const THINKING_PREVIEW_LIMIT = 160;

export function ThinkingActivity({ content, running, closed = false }) {
  const contentRef = useRef(null);
  const followRef = useRef(true);
  // Same rule as the transcript above: only real user input may end the
  // follow, because scroll events here also fire for the pin writes and for
  // anchoring adjustments caused by deltas landing above the scrollport.
  const userScrollingRef = useRef(false);
  const userClosedRef = useRef(false);
  const flattened = content.replace(/\s+/g, " ").trim();
  const preview = flattened.length > THINKING_PREVIEW_LIMIT
    ? `…${flattened.slice(-THINKING_PREVIEW_LIMIT)}`
    : flattened;

  const pinContentToLatest = useCallback(() => {
    const node = contentRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    userScrollingRef.current = false;
  }, []);

  // While streaming, keep the newest reasoning in view, scrolling instantly
  // in the same cadence as the transcript around it: a smooth inner scroll
  // restarted by every delta reads as tearing. The user pausing to read an
  // earlier part stops the follow until they return near the bottom.
  useEffect(() => {
    if (!running) {
      return;
    }
    if (followRef.current) {
      pinContentToLatest();
    }
  }, [content, running, pinContentToLatest]);

  // Controlled open state: an ended block (interrupt or completion) keeps
  // whatever is on screen instead of the open attribute removal folding it
  // abruptly; while the turn is still live, `closed` folds thinking that the
  // answer or a later tool call replaced.
  const [open, setOpen] = useState(Boolean(running));
  useEffect(() => {
    if (running) {
      setOpen(!userClosedRef.current);
    } else if (closed) {
      setOpen(false);
    }
  }, [running, closed]);

  return (
    <details
      className={`thinking-activity${running ? " thinking-running" : ""}`}
      open={open}
      onToggle={(event) => {
        const isOpen = event.currentTarget.open;
        setOpen(isOpen);
        if (!running) {
          // The collapsed preview shows the latest reasoning, so opening a
          // finished block lands on the same tail instead of the beginning.
          if (isOpen) {
            pinContentToLatest();
          }
          return;
        }
        userClosedRef.current = !isOpen;
        if (isOpen) {
          followRef.current = true;
          pinContentToLatest();
        }
      }}
    >
      <summary className="thinking-summary">
        <span className="thinking-label">{running ? "思考中" : "思考完成"}</span>
        {running && <span className="thinking-indicator running" aria-hidden="true" />}
        {running && <span className="thinking-preview">{preview}</span>}
        <ChevronRight className="thinking-chevron" size={14} aria-hidden="true" />
      </summary>
      <div
        className="thinking-content"
        ref={contentRef}
        onScroll={(event) => {
          if (!userScrollingRef.current) return;
          const node = event.currentTarget;
          followRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 32;
        }}
        onWheel={() => { userScrollingRef.current = true; }}
        onTouchStart={() => { userScrollingRef.current = true; }}
        onPointerDown={(event) => {
          if (event.target === event.currentTarget) userScrollingRef.current = true;
        }}
      ><MarkdownContent content={content} /></div>
    </details>
  );
}

const ActivityTextSection = memo(function ActivityTextSection({ section }) {
  const content = String(section.content || "").trim();
  if (!content) {
    return null;
  }
  if (section.title === "Assistant content") {
    return (
      <section className="activity-section assistant-content-section">
        <MarkdownContent content={content} />
      </section>
    );
  }
  return (
    <section className="activity-section">
      <pre className="activity-block">{content}</pre>
    </section>
  );
});

const ToolGroup = memo(function ToolGroup({ section, api }) {
  const safeCalls = Array.isArray(section.calls) ? section.calls : [];
  const [expanded, setExpanded] = useState(section.status === "running");

  useEffect(() => {
    if (section.status === "running") {
      setExpanded(true);
    }
  }, [section.status]);

  return (
    <section className="tool-group">
      <button
        className="tool-group-head"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span>{toolGroupTitle(section)}</span>
        <ChevronRight className="activity-chevron" aria-hidden="true" />
      </button>
      {expanded && (
        <div className="tool-group-body">
          {safeCalls.map((call, index) => (
            <ToolCallRow call={call} key={call.id || `${call.name}-${index}`} api={api} />
          ))}
        </div>
      )}
    </section>
  );
});

function ToolCallRow({ call, api }) {
  const [expanded, setExpanded] = useState(false);
  const [fullResult, setFullResult] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const hasResult = Boolean(call.result || call.resultRef);
  const tone = activityTone(call.status);

  async function toggleExpanded() {
    if (!hasResult) {
      return;
    }
    const nextExpanded = !expanded;
    setExpanded(nextExpanded);
    if (!nextExpanded || !call.resultRef || fullResult || loading || !api?.getToolResult) {
      return;
    }
    try {
      setLoading(true);
      setLoadError("");
      const response = await api.getToolResult(call.id);
      setFullResult(stringifyFullDetail(response.error || response.result || ""));
    } catch (err) {
      setLoadError(readableError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tool-call-row">
      <button
        className="tool-call-toggle"
        type="button"
        disabled={!hasResult}
        aria-expanded={hasResult ? expanded : undefined}
        onClick={toggleExpanded}
      >
        <span className="tool-call-name">{toolCallDisplayLabel(call)}</span>
        <span className={`tool-call-status ${tone}`}>{toolStatusLabel(call.status)}</span>
      </button>
      {expanded && hasResult && (
        <pre className="tool-call-result">
          {loading ? "正在加载结果…" : loadError || fullResult || call.result}
        </pre>
      )}
    </div>
  );
}

// Sliding tab indicator: one shared element per tab strip eases onto the
// active button instead of every button owning its own instant highlight.
// "pill" strips cover the whole button; "underline" strips keep their bar
// height in CSS and pin the bar to the button's bottom edge.
export function placeTabIndicator(nav) {
  if (!nav) {
    return false;
  }
  const indicator = nav.querySelector(".tab-indicator");
  const button = nav.querySelector("button.active");
  if (!indicator || !button) {
    return false;
  }
  if (nav.dataset.indicator === "underline") {
    const barHeight = 2;
    indicator.style.width = `${button.offsetWidth}px`;
    indicator.style.transform = `translate(${button.offsetLeft}px, ${button.offsetTop + button.offsetHeight - barHeight}px)`;
    return true;
  }
  indicator.style.width = `${button.offsetWidth}px`;
  indicator.style.height = `${button.offsetHeight}px`;
  indicator.style.transform = `translate(${button.offsetLeft}px, ${button.offsetTop}px)`;
  return true;
}

// The strip's nav remounts whenever the panel mode swaps content in and out;
// the callback ref re-attaches measurement to each new node so the indicator
// is placed before its first paint instead of sliding in from the origin.
function useSlidingTabIndicator(activeKey) {
  const navRef = useRef(null);
  const observerRef = useRef(null);
  const attachNav = useCallback((node) => {
    navRef.current = node;
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (!node) {
      return;
    }
    placeTabIndicator(node);
    // Panel collapse/expand and window resizes reflow the strip; the
    // observer re-measures so the indicator follows without re-renders.
    const observer = new ResizeObserver(() => placeTabIndicator(node));
    observer.observe(node);
    observerRef.current = observer;
  }, []);
  useLayoutEffect(() => {
    placeTabIndicator(navRef.current);
  }, [activeKey]);
  return attachNav;
}

export function TracePanel({ api, collapsed, contextSnapshots, events, activityEvents, activityMeta, traceMeta, settings, activeSession, onToggle }) {
  const sessionEvents = Array.isArray(activityEvents) ? activityEvents : [];
  const [panelMode, setPanelMode] = useState("Trace");
  const [tab, setTab] = useState("Overview");
  const [eventPrefix, setEventPrefix] = useState("all");
  const [expandedEventKeys, setExpandedEventKeys] = useState(() => new Set());
  const overviewTools = useMemo(() => traceToolEvents(sessionEvents), [sessionEvents]);
  const traceListRef = useRef(null);
  const attachModesNav = useSlidingTabIndicator(panelMode);
  const attachTabsNav = useSlidingTabIndicator(tab);
  // Tools and Changes read the session-wide activity feed; the raw event list
  // stays scoped to the current run.
  const tabSource = tab === "Tools" || tab === "Changes" ? sessionEvents : events;
  const tabEvents = useMemo(() => traceTabEvents(tab, tabSource), [tab, tabSource]);
  const prefixGroups = useMemo(
    () => (tab === "Events" ? eventPrefixGroups(tabEvents) : []),
    [tab, tabEvents],
  );
  const activePrefix = eventPrefix === "all" || prefixGroups.some((group) => group.prefix === eventPrefix)
    ? eventPrefix
    : "all";
  const visibleEvents = useMemo(
    () => filterTraceEventsByPrefix(tabEvents, activePrefix),
    [tabEvents, activePrefix],
  );
  const renderedEvents = useMemo(() => latestItems(visibleEvents, TRACE_RENDER_LIMIT), [visibleEvents]);
  const hiddenEventCount = Math.max(0, visibleEvents.length - renderedEvents.length);
  const renderedEventKeys = useMemo(
    () => renderedEvents.map((event, index) => traceEventKey(event, tab, index)),
    [renderedEvents, tab],
  );
  const expandedCount = renderedEventKeys.filter((key) => expandedEventKeys.has(key)).length;
  const sourceMeta = tab === "Tools" || tab === "Changes" ? activityMeta : traceMeta;
  const projectScopeId = activeSession?.scope_id === "conversation"
    ? ""
    : activeSession?.scope_id || scopeIdFromWorkspace(settings.workspace_root);

  useEffect(() => {
    setEventPrefix("all");
    setExpandedEventKeys(new Set());
  }, [tab, activeSession?.session_id, activeSession?.last_case_run_id]);

  useEffect(() => {
    if (!collapsed && tab !== "Config") {
      scrollTraceToLatest(traceListRef.current);
    }
  }, [collapsed, renderedEvents, tab]);

  return (
    <aside className="trace" aria-label="Trace inspector">
      <header className="trace-header">
        <div className="trace-title">
          <h3>{INSPECTOR_LABELS[panelMode]}</h3>
          <p>{panelMode === "Trace"
            ? "工具与文件活动为会话全量，事件为当前运行"
            : projectScopeId ? settings.workspace_root : "No project selected"}</p>
        </div>
        <button
          className="icon-button trace-toggle"
          title={collapsed ? "Expand trace" : "Collapse trace"}
          type="button"
          onClick={onToggle}
        >
          {collapsed ? <PanelRightOpen aria-hidden="true" /> : <PanelRightClose aria-hidden="true" />}
        </button>
        <div className="vertical-label">详情</div>
      </header>

      <nav className="inspector-modes" aria-label="Inspector modes" ref={attachModesNav} data-indicator="pill">
        <span className="tab-indicator" aria-hidden="true" />
        {["Trace", "Files", "PowerShell"].map((item) => (
          <button className={item === panelMode ? "active" : ""} key={item} type="button" onClick={() => setPanelMode(item)}>
            {INSPECTOR_LABELS[item]}
          </button>
        ))}
      </nav>

      {panelMode === "Files" ? (
        <FileExplorer api={api} scopeId={projectScopeId} workspaceRoot={projectScopeId ? settings.workspace_root : ""} />
      ) : panelMode === "PowerShell" ? (
        <PowerShellPanel api={api} scopeId={projectScopeId} workspaceRoot={projectScopeId ? settings.workspace_root : ""} />
      ) : (
      <div className="trace-mode-content">
        <nav className="trace-tabs" aria-label="Trace tabs" ref={attachTabsNav} data-indicator="underline">
          <span className="tab-indicator" aria-hidden="true" />
          {TRACE_TABS.map((item) => (
            <button className={item === tab ? "tab active" : "tab"} key={item} type="button" onClick={() => setTab(item)}>
              {INSPECTOR_LABELS[item]}
            </button>
          ))}
        </nav>

        {tab === "Overview" ? (
          <div className="run-overview">
            <span className="section-label">当前任务</span>
            <h4>{activeSession?.title || "还没有任务"}</h4>
            <p className="overview-status">{!events.length && !sessionEvents.length ? "执行任务后，这里会显示工具活动与文件记录。" : "执行记录已就绪，可按需查看工具结果与原始事件。"}</p>
            <div className="overview-metrics">
              <button type="button" onClick={() => setTab("Tools")}><strong>{overviewTools.length}</strong><span>工具调用</span></button>
              <button type="button" onClick={() => setTab("Changes")}><strong>{traceTabEvents("Changes", sessionEvents).length}</strong><span>文件活动</span></button>
              <button type="button" onClick={() => setTab("Events")}><strong>{events.length}</strong><span>原始事件</span></button>
            </div>
            <h5>最近工具活动</h5>
            {overviewTools.slice(-6).map((event, index) => { const key = `overview-${event.id || index}`; return <TraceEventRow key={key} event={event} tab="Tools" expanded={expandedEventKeys.has(key)} onToggle={() => setExpandedEventKeys((current) => toggleSetValue(current, key))} />; })}
            {!overviewTools.length && <p className="empty-state">暂无工具调用</p>}
            <details className="overview-identifiers"><summary>运行标识</summary><p>Session: {activeSession?.session_id || "—"}</p><p>Run: {activeSession?.last_case_run_id || "—"}</p></details>
          </div>
        ) : tab === "Config" ? (
        <div className="config-list">
          {configRows(settings).map(([key, value]) => (
            <div className="config-row" key={key}>
              <strong>{key}</strong>
              <span>{formatConfigValue(key, value)}</span>
            </div>
          ))}
        </div>
      ) : tab === "Context" ? (
        <ContextInspector snapshots={contextSnapshots} />
      ) : (
        <div className="trace-event-pane">
          <div className="trace-list-controls">
            <div className="trace-list-toolbar">
              <span>{visibleEvents.length} {tab.toLowerCase()}</span>
              <div className="trace-list-actions">
                <button
                  type="button"
                  disabled={renderedEventKeys.length === 0 || expandedCount === renderedEventKeys.length}
                  onClick={() => setExpandedEventKeys(new Set(renderedEventKeys))}
                >
                  Expand all
                </button>
                <button
                  type="button"
                  disabled={expandedCount === 0}
                  onClick={() => setExpandedEventKeys(new Set())}
                >
                  Collapse all
                </button>
              </div>
            </div>
            {tab === "Events" && prefixGroups.length > 0 && (
              <div className="trace-prefix-filters" aria-label="Filter events by prefix">
                <button
                  className={activePrefix === "all" ? "active" : ""}
                  type="button"
                  aria-pressed={activePrefix === "all"}
                  onClick={() => setEventPrefix("all")}
                >
                  <span>All</span><strong>{tabEvents.length}</strong>
                </button>
                {prefixGroups.map((group) => (
                  <button
                    className={activePrefix === group.prefix ? "active" : ""}
                    key={group.prefix}
                    type="button"
                    aria-pressed={activePrefix === group.prefix}
                    onClick={() => setEventPrefix(group.prefix)}
                  >
                    <span>{group.prefix}</span><strong>{group.count}</strong>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="trace-list" ref={traceListRef}>
            {visibleEvents.length === 0 && <div className="empty-state">No {tab.toLowerCase()} yet</div>}
            {traceWindowNotice(sourceMeta) && (
              <div className="empty-state compact">{traceWindowNotice(sourceMeta)}</div>
            )}
            {hiddenEventCount > 0 && (
              <div className="empty-state compact">Showing latest {renderedEvents.length} of {visibleEvents.length}</div>
            )}
            {renderedEvents.map((event, index) => {
              const key = renderedEventKeys[index];
              return (
                <TraceEventRow
                  event={event}
                  expanded={expandedEventKeys.has(key)}
                  tab={tab}
                  key={key}
                  onToggle={() => setExpandedEventKeys((current) => toggleSetValue(current, key))}
                />
              );
            })}
          </div>
        </div>
        )}
      </div>
      )}
    </aside>
  );
}

export function ContextInspector({ snapshots }) {
  const runs = useMemo(() => contextSnapshotRuns(snapshots), [snapshots]);
  const latestRun = runs[runs.length - 1] || null;
  const [runId, setRunId] = useState("");
  const [version, setVersion] = useState(null);
  const [snapshotId, setSnapshotId] = useState("");

  useEffect(() => {
    if (!runs.length) {
      setRunId("");
      setVersion(null);
      setSnapshotId("");
      return;
    }
    const selectedRun = runs.find((run) => run.id === runId) || latestRun;
    const selectedVersion = selectedRun.versions.find((item) => item.version === version)
      || selectedRun.versions[selectedRun.versions.length - 1];
    const selectedSnapshot = selectedVersion.snapshots.find((item) => item.snapshot_id === snapshotId)
      || selectedVersion.snapshots[selectedVersion.snapshots.length - 1];
    if (runId !== selectedRun.id) setRunId(selectedRun.id);
    if (version !== selectedVersion.version) setVersion(selectedVersion.version);
    if (snapshotId !== selectedSnapshot.snapshot_id) setSnapshotId(selectedSnapshot.snapshot_id);
  }, [runs, latestRun, runId, version, snapshotId]);

  if (!latestRun) {
    return <div className="empty-state context-empty">No model context captured yet</div>;
  }
  const selectedRun = runs.find((run) => run.id === runId) || latestRun;
  const selectedVersion = selectedRun.versions.find((item) => item.version === version)
    || selectedRun.versions[selectedRun.versions.length - 1];
  const selectedSnapshot = selectedVersion.snapshots.find((item) => item.snapshot_id === snapshotId)
    || selectedVersion.snapshots[selectedVersion.snapshots.length - 1];
  const stepIndex = selectedVersion.snapshots.findIndex((item) => item.snapshot_id === selectedSnapshot.snapshot_id);

  return (
    <div className="context-inspector">
      <div className="context-toolbar">
        <label>
          <span>Run</span>
          <select value={selectedRun.id} onChange={(event) => { setRunId(event.target.value); setVersion(null); }}>
            {runs.map((run, index) => (
              <option value={run.id} key={run.id}>{`Run ${index + 1} · ${shortId(run.id)}`}</option>
            ))}
          </select>
        </label>
        <div className="context-stepper" aria-label="Context step">
          <button type="button" disabled={stepIndex <= 0} onClick={() => setSnapshotId(selectedVersion.snapshots[stepIndex - 1].snapshot_id)}>‹</button>
          <span>Step {stepIndex + 1}/{selectedVersion.snapshots.length} · {contextSnapshotPhase(selectedSnapshot)}</span>
          <button type="button" disabled={stepIndex >= selectedVersion.snapshots.length - 1} onClick={() => setSnapshotId(selectedVersion.snapshots[stepIndex + 1].snapshot_id)}>›</button>
        </div>
      </div>
      <div className="context-version-strip" aria-label="Context versions">
        {selectedRun.versions.map((item) => (
          <button
            className={item.version === selectedVersion.version ? "context-version active" : "context-version"}
            key={item.version}
            type="button"
            onClick={() => {
              setVersion(item.version);
              setSnapshotId(item.snapshots[item.snapshots.length - 1].snapshot_id);
            }}
          >
            <strong>v{item.version}</strong>
            <span>{item.version === 0 ? "Original" : "Compressed"}</span>
          </button>
        ))}
      </div>
      <div className="context-meta">
        <span>{contextSnapshotPhase(selectedSnapshot)}</span>
        <span>{selectedSnapshot.message_count || 0} messages</span>
        <span>{selectedSnapshot.tool_count || 0} tools</span>
        <span>rev {selectedSnapshot.projection_revision || 0}</span>
      </div>
      <div className="context-variable-list">
        <ContextVariableRow index="S" role="system" value={{ role: "system", content: selectedSnapshot.system_prompt || "" }} />
        {(selectedSnapshot.messages || []).map((message, index) => (
          <ContextVariableRow
            index={String(index)}
            key={`${selectedSnapshot.snapshot_id}-${index}`}
            role={message.role || "message"}
            value={message}
          />
        ))}
      </div>
    </div>
  );
}

function ContextVariableRow({ index, role, value }) {
  const [expanded, setExpanded] = useState(false);
  const detail = safeJsonStringify(value);
  return (
    <div className={`context-variable-row role-${role}${expanded ? " expanded" : ""}`}>
      <button className="context-variable-main" type="button" aria-expanded={expanded} onClick={() => setExpanded((item) => !item)}>
        <span className="context-variable-chevron">{expanded ? "▾" : "▸"}</span>
        <span className="context-variable-index">{index}</span>
        <span className="context-variable-role">{role}</span>
        <span className="context-variable-preview">{contextMessageSummary(value) || "∅"}</span>
      </button>
      {expanded && (
        <div className="context-variable-detail">
          <button type="button" onClick={() => copyContextValue(detail)}>Copy</button>
          <pre>{detail}</pre>
        </div>
      )}
    </div>
  );
}

export function TraceEventRow({ event, expanded = false, tab, onToggle = () => {} }) {
  const details = traceEventDetails(event, tab);
  const content = (
    <>
      <span className="trace-event-heading">
        <strong className="trace-event-title">{eventTitle(event)}</strong>
        {details && <span className="trace-event-chevron" aria-hidden="true">{expanded ? "−" : "+"}</span>}
      </span>
      <span className="trace-event-summary">{eventSummary(event)}</span>
    </>
  );

  return (
    <div className={`trace-event${expanded ? " expanded" : ""}`}>
      <div className={`status-dot ${eventTone(event)}`} aria-hidden="true" />
      <div className="trace-event-content">
        {details ? (
          <button
            className="trace-event-toggle"
            type="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${eventTitle(event)}`}
            onClick={onToggle}
          >
            {content}
          </button>
        ) : (
          <div className="trace-event-text">{content}</div>
        )}
        {expanded && details && <pre className="trace-event-details">{details}</pre>}
      </div>
    </div>
  );
}

export function scrollTraceToLatest(element) {
  if (element) {
    element.scrollTop = element.scrollHeight;
  }
}

export function scrollTranscriptToLatest(element, smooth = false) {
  if (!element) {
    return;
  }
  if (smooth) {
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    return;
  }
  element.scrollTop = element.scrollHeight;
}

export function mergeContextSnapshots(...collections) {
  const merged = [];
  const seen = new Set();
  for (const collection of collections) {
    for (const snapshot of Array.isArray(collection) ? collection : []) {
      const id = String(snapshot?.snapshot_id || "");
      if (!id || seen.has(id)) continue;
      seen.add(id);
      merged.push(snapshot);
    }
  }
  return merged;
}

export function toggleSetValue(values, value) {
  const next = new Set(values);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function eventPrefixGroups(events) {
  const counts = new Map();
  for (const event of Array.isArray(events) ? events : []) {
    const prefix = eventTypePrefix(event?.type);
    counts.set(prefix, (counts.get(prefix) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([prefix, count]) => ({ prefix, count }))
    .sort((left, right) => left.prefix.localeCompare(right.prefix));
}

export function filterTraceEventsByPrefix(events, prefix) {
  const items = Array.isArray(events) ? events : [];
  if (!prefix || prefix === "all") return items;
  return items.filter((event) => eventTypePrefix(event?.type) === prefix);
}

function eventTypePrefix(type) {
  const value = String(type || "event");
  return value.split(".", 1)[0] || "event";
}

function traceEventKey(event, tab, index) {
  return `${tab}:${event.type || "event"}:${event.id || index}`;
}

export function contextSnapshotRuns(snapshots) {
  const runs = [];
  const byRun = new Map();
  for (const snapshot of mergeContextSnapshots(snapshots)) {
    const runId = String(snapshot.case_run_id || "unknown-run");
    let run = byRun.get(runId);
    if (!run) {
      run = { id: runId, versions: [], byVersion: new Map() };
      byRun.set(runId, run);
      runs.push(run);
    }
    const version = Math.max(0, Number(snapshot.compression_version) || 0);
    let group = run.byVersion.get(version);
    if (!group) {
      group = { version, snapshots: [] };
      run.byVersion.set(version, group);
      run.versions.push(group);
    }
    group.snapshots.push(snapshot);
  }
  return runs.map((run) => ({
    id: run.id,
    versions: run.versions.sort((left, right) => left.version - right.version),
  }));
}

export function contextSnapshotPhase(snapshot) {
  return snapshot?.phase === "response" ? "Response" : "Request";
}

function contextMessageSummary(message) {
  const content = String(message?.content || "").trim();
  if (content) return limitText(content.replace(/\s+/g, " "), 180);
  const calls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
  if (calls.length) return `calls ${calls.map((call) => call.name || "tool").join(", ")}`;
  const results = Array.isArray(message?.results) ? message.results : [];
  if (results.length) return results.map((result) => `${result.name || "tool"}: ${result.status || "result"}`).join(", ");
  return String(message?.kind || "");
}

function copyContextValue(value) {
  globalThis.navigator?.clipboard?.writeText?.(value).catch(() => {});
}

export function selectSessionMessages(pendingMessages, historyMessages) {
  return Array.isArray(pendingMessages) && pendingMessages.length > 0
    ? pendingMessages
    : historyMessages;
}

export function appendSessionLiveTraceEvent(cache, sessionId, currentEvents, event, limit = TRACE_RENDER_LIMIT) {
  const events = Array.isArray(currentEvents) ? currentEvents : [];
  if (event?.id && events.some((item) => item.id === event.id)) {
    return events;
  }
  const next = [...events, event];
  const limited = next.length > limit ? next.slice(-limit) : next;
  if (sessionId) {
    cache.set(sessionId, limited);
  }
  return limited;
}

export function messagesForRecovery(detail, assistantId) {
  const history = detail.history || [];
  const start = detail.run_history_start_index;
  if (!Number.isInteger(start) || start < 0 || start > history.length) {
    throw new Error("Invalid session recovery history boundary");
  }
  const user = history.slice(start).find((message) => message.role === "user");
  const attachments = user ? historyAttachments(user) : undefined;
  return [
    ...historyToMessages(history.slice(0, start)),
    ...(user ? [{ id: `${assistantId}-user`, role: messageDisplayRole(user), content: displayMessageContent(user),
      ...(user.created_at ? { createdAt: user.created_at } : {}),
      ...(attachments ? { attachments } : {}) }] : []),
    { id: assistantId, role: "assistant", content: "", sections: [], ...runTimingFields(user?.run_timing), status: "running" },
  ];
}

// Persisted user turns keep their attachment paths in data.attachments (the
// same shape the live send path uses), so restored transcripts re-render the
// media chips instead of silently dropping the images.
function historyAttachments(message) {
  const attachments = message?.data?.attachments;
  return Array.isArray(attachments) && attachments.length > 0 ? attachments : undefined;
}

export function historyToMessages(history) {
  const rendered = [];
  let index = 0;
  // The display window can start mid-turn (the history limit cuts into a long
  // run), so the messages before the first user message are one partial turn.
  // Grouping them keeps the run's timing and status on a single activity
  // block; one block per streamed round would repeat the run's failure header
  // for every round.
  while (index < history.length && String(history[index]?.role || "assistant") !== "user") {
    index += 1;
  }
  if (index > 0) {
    rendered.push(...renderTurnSegment(history.slice(0, index), `history-${index}`));
  }
  while (index < history.length) {
    const message = history[index] || {};
    const content = displayMessageContent(message);
    // An attachment-only turn persists with empty text; the chips alone must
    // keep the user message visible across reloads.
    const attachments = historyAttachments(message);
    if (content || attachments) {
      rendered.push({ id: `history-${index}`, role: messageDisplayRole(message), content,
        ...(message.created_at ? { createdAt: message.created_at } : {}),
        ...(message.checkpoint_sequence ? { checkpointSequence: message.checkpoint_sequence } : {}),
        ...(message.kind === "lara.user.steering" ? { steeringInputId: message.data?.input_id } : {}),
        ...(attachments ? { attachments } : {}),
      });
    }
    const segment = [];
    index += 1;
    while (index < history.length && String(history[index]?.role || "") !== "user") {
      segment.push(history[index]);
      index += 1;
    }
    rendered.push(...renderTurnSegment(segment, `history-${index}`, message.run_timing));
  }
  return rendered.filter(Boolean);
}

function renderTurnSegment(segment, idPrefix, timing = segment.find((message) => message?.run_timing)?.run_timing) {
  let lastToolCallIndex = -1;
  segment.forEach((message, index) => {
    if (String(message?.role || "") === "assistant" && toolCallsFromMessage(message).length > 0) {
      lastToolCallIndex = index;
    }
  });
  let finalAssistantIndex = -1;
  segment.forEach((message, index) => {
    if (
      index > lastToolCallIndex &&
      String(message?.role || "") === "assistant" &&
      cleanContent(String(message?.content || "")).trim() &&
      toolCallsFromMessage(message).length === 0
    ) {
      finalAssistantIndex = index;
    }
  });

  let sections = [];
  let finalContent = "";
  // Media generated on intermediate rounds (image, then a tool call, then
  // text) must survive the collapse into one assistant bubble, so collect
  // every assistant media block in conversation order.
  const media = segment.flatMap((message) =>
    String(message?.role || "") === "assistant" ? validMediaBlocks(message?.media) : [],
  );
  segment.forEach((message, index) => {
    const role = String(message?.role || "");
    const content = cleanContent(String(message?.content || ""));
    const reasoning = cleanContent(String(message?.metadata?.reasoning_content || message?.reasoning_content || ""));
    const toolCalls = toolCallsFromMessage(message);
    if (role === "assistant") {
      if (reasoning) {
        sections = appendTextSection(sections, "Thinking", reasoning);
      }
      if (index === finalAssistantIndex) {
        finalContent = content;
        return;
      }
      if (content) {
        sections = appendTextSection(sections, "Assistant content", content);
      }
      sections = appendToolCallsSection(sections, toolCalls.map(toolCallState));
      return;
    }
    if (role === "tool") {
      for (const activity of toolMessageResultActivities(message)) {
        sections = applyToolResultToSections(sections, activity);
      }
    }
  });
  if (sections.length === 0 && !finalContent && !media.length) {
    return [];
  }
  const usage = summarizeMessageUsage(
    segment
      .filter((message) => String(message?.role || "") === "assistant")
      .map((message) => message?.usage),
  );
  // Fork-at-message needs the boundary of the whole turn: the last checkpoint
  // row inside the segment, so the fork inherits everything shown so far.
  const lastCheckpointSequence = segment.reduce(
    (max, item) => Math.max(max, Number(item?.checkpoint_sequence) || 0),
    0,
  );
  const createdAt = segmentCreatedAt(segment, finalAssistantIndex);
  return [
    {
      id: `${idPrefix}-assistant`,
      role: "assistant",
      content: finalContent,
      status: "success",
      ...runTimingFields(timing),
      sections: finalizeToolSections(sections),
      ...(media.length ? { media } : {}),
      ...(lastCheckpointSequence ? { checkpointSequence: lastCheckpointSequence } : {}),
      ...(createdAt ? { createdAt } : {}),
      ...(usage ? { usage } : {}),
    },
  ];
}

// The answer's own checkpoint time is the turn's time; fall back to the
// latest timestamp in the segment when the final message omits it.
function segmentCreatedAt(segment, finalAssistantIndex) {
  const answer = finalAssistantIndex >= 0 ? segment[finalAssistantIndex] : undefined;
  if (typeof answer?.created_at === "string" && answer.created_at) {
    return answer.created_at;
  }
  for (let index = segment.length - 1; index >= 0; index -= 1) {
    const createdAt = segment[index]?.created_at;
    if (typeof createdAt === "string" && createdAt) {
      return createdAt;
    }
  }
  return undefined;
}

function apiEventToTraceEvent(event) {
  const seq = Number.isFinite(event.sequence) ? event.sequence : Date.now() + Math.random();
  return {
    id: `${event.execution_id || "execution"}-${seq}`,
    type: event.kind || "runtime.event",
    timestamp: "",
    actor: "runtime",
    payload: tracePreviewPayload(event.data || {}),
  };
}

function tracePreviewPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return {};
  }
  const preview = { ...payload };
  if (typeof preview.content === "string") {
    preview.content = limitText(preview.content, TOOL_RESULT_PREVIEW_LIMIT);
  }
  if (preview.payload && typeof preview.payload === "object") {
    preview.payload = { ...preview.payload };
    if (typeof preview.payload.content === "string") {
      preview.payload.content = limitText(preview.payload.content, TOOL_RESULT_PREVIEW_LIMIT);
    }
  }
  return preview;
}

export function splitAssistantForSteering(items, assistantId, inputId, content, newAssistantId, now = Date.now(), attachments) {
  // Deduplicate steering boundaries already rendered from history or an earlier event.
  if (items.some((item) => item.steeringInputId === inputId || item.id === `steering-${inputId}`)) return items;
  const user = {
    id: `steering-${inputId}`,
    role: "user",
    content,
    steeringInputId: inputId,
    ...(Array.isArray(attachments) && attachments.length ? { attachments } : {}),
  };
  const index = items.findIndex((item) => item.id === assistantId);
  const newAssistant = {
    id: newAssistantId,
    role: "assistant",
    content: "",
    status: "running",
    startedAt: now,
    endedAt: null,
    sections: [],
  };
  if (index < 0) {
    return [...items, user, newAssistant];
  }
  const previous = items[index] || {};
  const finalizedPrevious = {
    ...previous,
    // Freeze current partial content and activity
    endedAt: now,
    status: previous.status === "error" ? "error" : "success",
    sections: finalizeToolSections(previous.sections || []),
  };
  return [
    ...items.slice(0, index),
    finalizedPrevious,
    user,
    newAssistant,
    ...items.slice(index + 1),
  ];
}

// A steering input is shown as soon as it is sent, but it only belongs to the
// transcript once the runtime reports the ReAct boundary that consumed it.
export function addPendingSteering(pending, sessionId, entry) {
  if (!sessionId) {
    return pending;
  }
  const current = pending[sessionId] || [];
  if (current.some((item) => item.inputId === entry.inputId)) {
    return pending;
  }
  return { ...pending, [sessionId]: [...current, { ...entry, stale: false }] };
}

export function resolvePendingSteering(pending, sessionId, inputId) {
  const current = pending[sessionId];
  if (!current?.length) {
    return pending;
  }
  const next = current.filter((item) => item.inputId !== inputId);
  if (next.length === current.length) {
    return pending;
  }
  return next.length ? { ...pending, [sessionId]: next } : omitKey(pending, sessionId);
}

export function stalePendingSteerings(pending, sessionId) {
  const current = pending[sessionId];
  if (!current?.length || current.every((item) => item.stale)) {
    return pending;
  }
  return { ...pending, [sessionId]: current.map((item) => ({ ...item, stale: true })) };
}

// A reloaded transcript is the source of truth for which steering inputs the
// runtime consumed: the live boundary event can be missed when the turn ends
// or the stream tears down first, and the stale chip would otherwise claim
// "未生效" for an input that history shows was applied.
export function reconcilePendingSteerings(pending, sessionId, messages) {
  const current = pending[sessionId];
  if (!current?.length) {
    return pending;
  }
  const consumed = new Set(
    (Array.isArray(messages) ? messages : [])
      .map((message) => message?.steeringInputId)
      .filter(Boolean),
  );
  if (!consumed.size) {
    return pending;
  }
  const next = current.filter((item) => !consumed.has(item.inputId));
  if (next.length === current.length) {
    return pending;
  }
  return next.length ? { ...pending, [sessionId]: next } : omitKey(pending, sessionId);
}

function projectLiveExecutionEvent(setMessages, assistantId, event) {
  setMessages((items) => {
    let changed = false;
    const next = items.map((item) => {
      if (item.id !== assistantId) {
        return item;
      }
      const projected = projectLiveAssistantEvent(item, event);
      changed = changed || projected !== item;
      return projected;
    });
    return changed ? next : items;
  });
}

export function projectLiveAssistantEvent(message, event, now = Date.now()) {
  const kind = String(event?.kind || "");
  const payload = event?.data && typeof event.data === "object" ? event.data : {};
  if (payload.run_timing) {
    message = { ...message, ...runTimingFields(payload.run_timing) };
  }

  if (kind === "model.reasoning.delta") {
    const delta = String(payload.text || "");
    return delta ? { ...message, sections: appendDeltaSection(message.sections, "Thinking", delta) } : message;
  }

  if (kind === "model.text.delta") {
    return { ...message, content: `${message.content || ""}${String(payload.text || "")}` };
  }

  if (kind === "model.tool_call.completed" || kind === "tool.started"
      || (kind === "lara.runtime.message" && payload.role === "assistant" && toolCallsFromMessage(payload).length)) {
    if (kind === "tool.started" && isInternalToolId(payload.tool_id)) {
      const callId = String(payload.call_id || payload.tool_call_id || "");
      const internalIds = Array.isArray(message.internalToolCallIds) ? message.internalToolCallIds : [];
      if (!callId || internalIds.includes(callId)) {
        return message;
      }
      return { ...message, internalToolCallIds: [...internalIds, callId] };
    }
    const sections = Array.isArray(message.sections) ? message.sections : [];
    const knownIds = new Set(sections.flatMap((section) => (section.calls || []).map((call) => call.id)));
    const calls = kind === "lara.runtime.message" ? toolCallsFromMessage(payload) : [{
      id: payload.call_id || payload.tool_call_id,
      name: payload.name || payload.tool_name,
      arguments: payload.arguments ?? payload.args,
    }];
    let nextSections = sections;
    const newCalls = [];
    for (const raw of calls) {
      const call = toolCallState(raw);
      if (call.id && knownIds.has(call.id)) {
        nextSections = backfillToolCallState(nextSections, raw);
        continue;
      }
      newCalls.push(call);
    }
    if (!newCalls.length) {
      // Pure backfill: keep streaming content where it is instead of moving it
      // into a section for a row that already exists.
      return nextSections === sections ? message : { ...message, sections: nextSections };
    }
    if (String(message.content || "").trim()) {
      nextSections = appendTextSection(nextSections, "Assistant content", message.content);
    }
    return { ...message, content: "", sections: appendToolCallsSection(nextSections, newCalls) };
  }

  if (kind === "tool.result" || (kind === "lara.runtime.message" && payload.role === "tool")) {
    const activities = kind === "tool.result"
      ? [toolResultActivity(payload, payload)]
      : toolMessageResultActivities(payload);
    let sections = message.sections || [];
    for (const activity of activities) {
      sections = applyToolResultToSections(sections, activity);
    }
    return {
      ...message,
      sections,
    };
  }

  if (kind === "tool.completed" || kind === "tool.failed" || kind === "tool.cancelled") {
    const callId = String(payload.call_id || "");
    if ((message.internalToolCallIds || []).includes(callId)) {
      return message;
    }
    const failed = kind !== "tool.completed";
    const existing = (message.sections || []).flatMap((section) => section.calls || []).find((call) => call.id === callId);
    const result = {
      toolCallId: callId,
      content: failed ? String(payload.error || payload.error_kind || kind.replace("tool.", "")) : existing?.result || "",
      resultRef: existing?.resultRef,
      resultSize: existing?.resultSize,
      truncated: existing?.truncated,
      status: failed ? "error" : "success",
    };
    return {
      ...message,
      sections: applyToolResultToSections(message.sections || [], result),
    };
  }

  if (kind === "execution.completed") {
    return {
      ...message,
      content: cleanContent(String(message.content || "")),
      endedAt: message.endedAt ?? now,
      sections: finalizeToolSections(message.sections || []),
      status: "success",
    };
  }

  if (kind === "execution.cancelled") {
    return {
      ...message,
      // An interrupt keeps the streamed partial answer on screen; the cancel
      // notice only fills in when nothing was streamed.
      content: String(message.content || payload.reason || "对话已取消。"),
      endedAt: message.endedAt ?? now,
      sections: finalizeToolSections(message.sections || []),
      status: "error",
    };
  }

  if (kind === "lara.transport.error" || kind === "execution.failed" || kind === "execution.deadline_exceeded") {
    return {
      ...message,
      content: `错误：${payload.error || payload.message || "对话执行失败"}`,
      endedAt: message.endedAt ?? now,
      sections: finalizeToolSections(message.sections || []),
      status: "error",
    };
  }

  return message;
}

function activityLiveStatus(message) {
  if (message.status !== "running") {
    return "";
  }
  const sections = Array.isArray(message.sections) ? message.sections : [];
  const liveSection = latestLiveStatusSection(sections);
  if (liveSection) {
    if (liveSection.type === "tools") {
      return latestToolDescriptionFromSection(liveSection);
    }
    // Actively streaming reasoning is its own live state, shown by the
    // thinking block itself. Thinking or text behind finished tool calls
    // means the next model request is already in flight.
    const isStreamingThinking = liveSection.type === "text"
      && liveSection.title === "Thinking"
      && liveSection === sections[sections.length - 1];
    return isStreamingThinking ? "" : "模型调用中";
  }
  // Turn start and the gap after tool results: the model request is in
  // flight until reasoning, tool calls, or streamed text replaces it.
  return hasVisibleAssistantContent(message) ? "" : "模型调用中";
}

function latestLiveStatusSection(sections) {
  for (let index = sections.length - 1; index >= 0; index -= 1) {
    const section = sections[index];
    if (!section) {
      continue;
    }
    if (section.type === "text" && section.title === "Thinking") {
      return section;
    }
    if (section.type === "tools" && section.status === "running") {
      return section;
    }
    if (section.type === "text" && section.title === "Assistant content") {
      return section;
    }
  }
  return null;
}

function latestToolDescriptionFromSection(section) {
  const calls = Array.isArray(section?.calls) ? section.calls : [];
  for (let callIndex = calls.length - 1; callIndex >= 0; callIndex -= 1) {
    const call = calls[callIndex];
    const description = String(call.description || "").trim() || toolCallDisplayLabel(call);
    if (description) {
      return description;
    }
  }
  return "";
}

function hasVisibleAssistantContent(message) {
  if (cleanContent(String(message.content || "")).trim()) {
    return true;
  }
  return (Array.isArray(message.sections) ? message.sections : []).some(
    (section) =>
      section.type === "text" &&
      section.title === "Assistant content" &&
      cleanContent(String(section.content || "")).trim(),
  );
}

function appendDeltaSection(sections, title, delta) {
  const safeSections = Array.isArray(sections) ? [...sections] : [];
  const last = safeSections[safeSections.length - 1];
  if (last?.type === "text" && last.title === title) {
    safeSections[safeSections.length - 1] = { ...last, content: `${last.content || ""}${delta}` };
    return safeSections;
  }
  return [
    ...safeSections,
    {
      id: `text-${Date.now()}-${Math.random()}`,
      type: "text",
      title,
      content: delta,
    },
  ];
}

function appendTextSection(sections, title, content) {
  const value = String(content || "").trim();
  if (!value) {
    return Array.isArray(sections) ? sections : [];
  }
  return [
    ...(Array.isArray(sections) ? sections : []),
    {
      id: `text-${Date.now()}-${Math.random()}`,
      type: "text",
      title,
      content: value,
    },
  ];
}

function appendToolCallsSection(sections, calls) {
  const safeCalls = Array.isArray(calls) ? calls.filter(Boolean) : [];
  if (!safeCalls.length) {
    return Array.isArray(sections) ? sections : [];
  }
  const nextSections = Array.isArray(sections) ? [...sections] : [];
  const lastSection = nextSections[nextSections.length - 1];
  if (lastSection?.type === "tools") {
    // Only batches rendered back to back share one group; thinking or text
    // between batches keeps each group at its chronological position.
    nextSections[nextSections.length - 1] = {
      ...lastSection,
      status: "running",
      calls: mergeToolCalls(lastSection.calls || [], safeCalls),
    };
    return nextSections;
  }
  nextSections.push({
    id: `tools-${Date.now()}-${Math.random()}`,
    type: "tools",
    status: "running",
    calls: safeCalls,
  });
  return nextSections;
}

function applyToolResultToSections(sections, result) {
  const nextSections = Array.isArray(sections)
    ? sections.map((section) =>
        section.type === "tools" ? { ...section, calls: [...(section.calls || [])] } : section,
      )
    : [];
  for (let index = nextSections.length - 1; index >= 0; index -= 1) {
    const section = nextSections[index];
    if (section.type !== "tools") {
      continue;
    }
    if (result.toolCallId && !(section.calls || []).some((call) => call.id === result.toolCallId)) {
      continue;
    }
    const calls = applyToolResult(section.calls || [], result);
    if (calls !== section.calls) {
      nextSections[index] = {
        ...section,
        calls,
        status: calls.some((call) => call.status === "running") ? "running" : "done",
      };
      return nextSections;
    }
  }
  return appendToolCallsSection(nextSections, applyToolResult([], result));
}

function finalizeToolSections(sections) {
  return (Array.isArray(sections) ? sections : []).map((section) => {
    if (section.type !== "tools") {
      return section;
    }
    const calls = (section.calls || []).map((call) =>
      call.status === "running" && !call.backgroundRunning ? { ...call, status: "success" } : call,
    );
    const hasError = calls.some((call) => call.status === "error");
    const hasRunning = calls.some((call) => call.status === "running");
    return { ...section, calls, status: hasRunning ? "running" : hasError ? "error" : "done" };
  });
}

function toolGroupTitle(section) {
  const calls = Array.isArray(section.calls) ? section.calls : [];
  const count = calls.length;
  const verb = section.status === "running" ? "正在执行" : "已执行";
  return `${verb} ${count} 个工具调用`;
}

function traceTabEvents(tab, events) {
  if (tab === "Tools") {
    return traceToolEvents(events);
  }
  if (tab === "Changes") {
    return events.filter((event) => {
      const type = String(event.type || "");
      return type.startsWith("file.") || type === "diff.created";
    });
  }
  return events;
}

export function traceWindowNotice(meta) {
  if (!meta?.truncated) {
    return "";
  }
  return `已载入最近 ${meta.shown || 0} / ${meta.total || 0} 条，更早的记录未载入`;
}

export function dedupeTraceEvents(events) {
  const items = [];
  const seen = new Set();
  for (const event of Array.isArray(events) ? events : []) {
    const id = String(event?.id || "");
    if (id) {
      if (seen.has(id)) continue;
      seen.add(id);
    }
    items.push(event);
  }
  return items;
}

// Runtime-synthesized bookkeeping calls (deferred file effects) are executed
// without a model step: they emit tool.started/tool.completed with a tool_id
// but no name and no model.tool_call.completed, so chat rows built from them
// can only show the "tool" placeholder. They are tracked and hidden instead.
const INTERNAL_TOOL_ID_PREFIX = "lara.internal.";

function isInternalToolId(toolId) {
  return String(toolId || "").startsWith(INTERNAL_TOOL_ID_PREFIX);
}

// A row may already exist when the model completion lands (out-of-order
// started event, resumed stream); the completion carries the authoritative
// name and arguments, so fill the row in place instead of dropping the event.
// Only concrete values patch the row: the raw event has no "tool" fallback.
function backfillToolCallState(sections, toolCall) {
  const id = toolCallId(toolCall);
  const name = String(toolCall?.name || toolCall?.tool_name || toolCall?.function?.name || "");
  const argumentsText = toolCallArguments(toolCall);
  const description = argumentsText ? toolCallDescription(argumentsText) : "";
  let changed = false;
  const nextSections = sections.map((section) => {
    if (section.type !== "tools") {
      return section;
    }
    const calls = section.calls || [];
    const index = calls.findIndex((call) => call.id === id);
    if (index < 0) {
      return section;
    }
    const call = calls[index];
    const patch = {
      ...(name && name !== call.name ? { name } : {}),
      ...(description && description !== call.description ? { description } : {}),
      ...(argumentsText && argumentsText !== call.arguments ? { arguments: argumentsText } : {}),
    };
    if (!Object.keys(patch).length) {
      return section;
    }
    changed = true;
    const next = [...calls];
    next[index] = { ...call, ...patch };
    return { ...section, calls: next };
  });
  return changed ? nextSections : sections;
}

export function traceToolEvents(events) {
  const items = [];
  const byCallId = new Map();
  const internalCallIds = new Set((Array.isArray(events) ? events : [])
    .filter((event) => String(event?.type || "") === "tool.started" && isInternalToolId(event?.payload?.tool_id))
    .map((event) => String(event?.payload?.call_id || event?.payload?.tool_call_id || ""))
    .filter(Boolean));

  function upsert(callId, patch) {
    const key = callId || `tool-${items.length}`;
    let item = callId ? byCallId.get(callId) : null;
    if (!item) {
      item = {
        id: key,
        type: "tool.call",
        payload: {
          tool_call_id: callId,
          tool_name: callId || "tool",
          trace_tool: true,
          has_call: false,
          has_result: false,
          status: "running",
        },
      };
      items.push(item);
      if (callId) {
        byCallId.set(callId, item);
      }
    }
    // Replayed call records can arrive after a live result; never regress to running.
    if (patch.status === "running" && item.payload.status !== "running") {
      patch = { ...patch, status: item.payload.status };
    }
    item.payload = { ...item.payload, ...patch };
    return item;
  }

  for (const event of events) {
    const type = String(event.type || "");
    const payload = event.payload || {};

    if (type === "tool.call" || type === "model.tool_call.completed" || type === "tool.started") {
      const callId = String(payload.model_tool_call_id || payload.tool_call_id || payload.call_id || payload.id || event.id || "");
      if (internalCallIds.has(callId)) {
        continue;
      }
      const previous = byCallId.get(callId)?.payload;
      upsert(callId, {
        tool_call_id: callId,
        tool_name: String(payload.tool_name || payload.name || previous?.tool_name || "tool"),
        arguments: payload.args !== undefined || payload.arguments !== undefined
          ? stringifyToolArgs(payload.args ?? payload.arguments) : previous?.arguments || "",
        call_event: event,
        has_call: true,
        status: "running",
      });
      continue;
    }

    if (["tool.completed", "tool.failed", "tool.cancelled"].includes(type)) {
      const callId = String(payload.call_id || payload.tool_call_id || "");
      if (internalCallIds.has(callId)) {
        continue;
      }
      const failed = type !== "tool.completed";
      const patch = {
        result_event: event,
        status: failed ? "error" : "success",
      };
      if (failed) {
        patch.has_result = true;
        patch.result = stringifyDetail(payload.error || payload.error_kind || type);
      }
      upsert(callId, patch);
      continue;
    }

    if (type === "tool.result") {
      const callId = String(payload.model_tool_call_id || payload.tool_call_id || "");
      const result = toolResultActivity(payload, payload);
      const patch = {
        tool_call_id: callId,
        result_event: event,
        has_result: true,
        result: result.content,
        result_ref: payload.result_ref || "",
        result_size: payload.result_size || 0,
        truncated: Boolean(payload.truncated),
        status: result.status,
      };
      if (payload.tool_name) {
        patch.tool_name = String(payload.tool_name);
      }
      upsert(callId, patch);
      continue;
    }

    if (type === "runtime.message" || type === "lara.runtime.message") {
      if (payload.role === "assistant") {
        for (const call of toolCallsFromMessage(payload)) {
          const state = toolCallState(call);
          upsert(state.id, {
            tool_call_id: state.id,
            tool_name: state.name,
            arguments: state.arguments,
            call_event: event,
            has_call: true,
            status: "running",
          });
        }
      } else if (payload.role === "tool") {
        const result = toolResultActivity(payload);
        if (result.toolCallId) {
          upsert(result.toolCallId, {
            tool_call_id: result.toolCallId,
            result_event: event,
            has_result: true,
            result: result.content,
            status: result.status,
          });
        }
      }
    }
  }

  return items;
}

function configRows(settings) {
  return [
    ["workspace", settings.workspace_root],
    ["lara_root", settings.lara_root],
    ["agent", settings.agent],
    ["profile", settings.profile],
    ["routes", settings.routes],
    ["fallback", settings.fallback],
    ["max_steps", settings.max_steps],
    ["context_window", settings.context_window],
    ["compression_trigger", compressionTriggerLabel(settings.context_window, settings.context_compression_trigger_ratio)],
    ["user_lara_root", settings.user_lara_root],
  ];
}

export function formatConfigValue(key, value) {
  if (key === "routes") {
    const routes = Array.isArray(value) ? value : [];
    if (routes.length === 0) {
      return "-";
    }
    return routes.map((route, index) => {
      const id = String(route?.id || `route-${index + 1}`);
      const provider = String(route?.provider || "unknown");
      const model = String(route?.model_name || route?.model || "unknown");
      return `${id}  ${provider} / ${model}`;
    }).join("\n");
  }
  if (key === "fallback") {
    return Array.isArray(value) && value.length ? value.join(" → ") : "-";
  }
  if (value === undefined || value === null || value === "") {
    return "-";
  }
  return String(value);
}

function flattenSessionGroups(groups) {
  return groups.flatMap((group) => group.sessions || []);
}

export function firstSessionIdInScope(groups, activeScopeId) {
  const activeGroup = (groups || []).find((group) => group.scope?.scope_id === activeScopeId);
  return activeGroup?.sessions?.[0]?.session_id || "";
}

function latestItems(items, limit) {
  if (!Array.isArray(items) || items.length <= limit) {
    return items;
  }
  return items.slice(items.length - limit);
}

function applyRunningSessionStatus(groups, runningSessionIds) {
  if (!Object.keys(runningSessionIds).length) {
    return groups;
  }
  return groups.map((group) => ({
    ...group,
    sessions: (group.sessions || []).map((session) =>
      runningSessionIds[session.session_id] ? { ...session, last_case_run_status: "running" } : session,
    ),
  }));
}

function omitKey(items, key) {
  if (!items[key]) {
    return items;
  }
  const { [key]: _removed, ...rest } = items;
  return rest;
}

function scopeIdFromWorkspace(workspaceRoot) {
  return workspaceRoot ? `project:${workspaceRoot}` : "";
}

function compactLayoutMatches() {
  return Boolean(globalThis.window?.matchMedia?.(COMPACT_LAYOUT_QUERY).matches);
}

function sessionFromDetail(detail) {
  return {
    ...(detail?.session || {}),
    selectable_models: detail?.selectable_models || [],
  };
}

function sessionDetailSignature(session) {
  return [
    session?.session_id || "",
    session?.updated_at || "",
    session?.last_case_run_id || "",
    session?.last_case_run_status || "",
    session?.model_group_name || "",
    session?.selected_model_key || "",
    stableListSignature(session?.selectable_models || [], modelSummarySignature),
  ].join("|");
}

function sessionFreshnessSignature(session) {
  if (!session?.session_id) return "";
  return [
    session.session_id,
    session.updated_at || "",
    session.last_case_run_id || "",
    session.last_case_run_status || "",
    session.model_group_name || "",
    session.selected_model_key || "",
  ].join("|");
}

export function activeSessionNeedsRefresh(activeSession, listRecord) {
  return Boolean(
    activeSession?.session_id
    && activeSession.session_id === listRecord?.session_id
    && sessionFreshnessSignature(activeSession) !== sessionFreshnessSignature(listRecord)
  );
}

export function activeSessionRunAwaitingAttach(activeSession, listRecord, runningIds) {
  // A scheduler-driven turn leaves the list record running while this client
  // has no live stream for the session: the execution id is published only
  // after the runtime starts the run, and a failed attach also drops the
  // running flag. Keep probing so the session follows the live turn instead
  // of showing an unattachable spinner.
  return Boolean(
    activeSession?.session_id
    && activeSession.session_id === listRecord?.session_id
    && listRecord?.last_case_run_status === "running"
    && !runningIds?.[activeSession.session_id]
  );
}

export function rememberSessionView(cache, sessionId, view, limit = 5) {
  if (!cache || !sessionId) return;
  cache.delete(sessionId);
  cache.set(sessionId, view);
  while (cache.size > limit) {
    cache.delete(cache.keys().next().value);
  }
}

function logSessionSwitchPerformance({ sessionId, startedAt, historyCount, historyTotal }) {
  const frame = globalThis.window?.requestAnimationFrame?.bind(globalThis.window);
  const now = globalThis.performance?.now?.bind(globalThis.performance) || Date.now;
  if (!frame || typeof globalThis.console?.info !== "function") return;
  frame(() => frame(() => {
    globalThis.console.info("[performance] session_switch", {
      session_id: sessionId,
      duration_ms: Math.round((now() - startedAt) * 10) / 10,
      history_count: historyCount,
      history_total: historyTotal,
    });
  }));
}

function sessionHistorySignature(detail) {
  const history = detail?.history || [];
  return [
    detail?.session?.session_id || "",
    detail?.history_total ?? history.length,
    detail?.history_truncated ? "truncated" : "complete",
    stableListSignature(history, historyMessageSignature),
  ].join("|");
}

function traceResponseSignature(trace, key) {
  const items = trace?.[key] || [];
  const totalKey = key === "events" ? "events_total" : "context_snapshots_total";
  const truncatedKey = key === "events" ? "events_truncated" : "context_snapshots_truncated";
  return [
    trace?.session_id || "",
    trace?.case_run_id || "",
    trace?.[totalKey] ?? items.length,
    trace?.[truncatedKey] ? "truncated" : "complete",
    stableListSignature(items, key === "events" ? traceEventSignature : contextSnapshotSignature),
  ].join("|");
}

function stableListSignature(items, itemSignature) {
  if (!Array.isArray(items) || !items.length) {
    return "0";
  }
  const first = itemSignature(items[0]);
  const last = itemSignature(items[items.length - 1]);
  return `${items.length}:${first}:${last}`;
}

function modelSummarySignature(model) {
  return `${model?.model_key || ""}:${model?.provider || ""}:${model?.model_id || ""}`;
}

function historyMessageSignature(message) {
  return [
    message?.role || "",
    message?.turn_id || "",
    message?.checkpoint_id || "",
    message?.case_run_id || "",
    message?.run_timing?.case_run_id || "",
    message?.run_timing?.status || "",
    compactValueSignature(message?.content),
  ].join(",");
}

function traceEventSignature(event) {
  const payload = event?.payload || {};
  return [
    event?.id || "",
    event?.type || "",
    payload.sequence ?? "",
    payload.kind || "",
    payload.tool_call_id || payload.call_id || payload.model_tool_call_id || "",
    payload.status || "",
  ].join(",");
}

function contextSnapshotSignature(snapshot) {
  return [
    snapshot?.snapshot_id || "",
    snapshot?.case_run_id || "",
    snapshot?.turn_id || "",
    snapshot?.compression_version ?? "",
    snapshot?.projection_revision ?? "",
  ].join(",");
}

function compactValueSignature(value) {
  if (typeof value !== "string") {
    return value === undefined || value === null ? "" : String(value);
  }
  return `${value.length}:${value.slice(0, 96)}:${value.slice(-48)}`;
}

export function shouldSubmitComposer(event) {
  return event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing;
}

export function syncComposerDraft(drafts, key, value) {
  if (value) drafts.set(key, value);
  else drafts.delete(key);
}

export function replayEventAgeMs(event, now = Date.now()) {
  const timestamp = Number(event?.timestamp_unix_ns);
  // No usable timestamp: treat as live so unknown event shapes keep the
  // current always-paint behavior.
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    return 0;
  }
  return Math.max(0, now - timestamp / 1e6);
}

export async function initializeWorkbench(
  refresh,
  {
    attempts = 20,
    retryDelay = () => new Promise((resolve) => globalThis.setTimeout(resolve, 250)),
  } = {},
) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await refresh();
    } catch (err) {
      lastError = err;
      if (!isTransientFetchError(err) || attempt === attempts - 1) {
        throw err;
      }
      await retryDelay();
    }
  }
  throw lastError;
}

function isTransientFetchError(error) {
  const message = String(error?.message || error).toLowerCase();
  return message.includes("failed to fetch") || message.includes("fetch failed") || message.includes("networkerror");
}

function primaryModel(settings) {
  const keys = settings.model_groups?.[settings.default_model_group]?.models || [];
  return settings.models?.[keys[0]]?.model_id || "unconfigured model";
}

// Media capability summary for the session's model picker, so a multimodal
// main model is recognizable before selection; models without declared
// media capabilities keep the plain provider/model description.
function modelCapabilitySummary(model, settings) {
  const base = `${model.provider} / ${model.model_id}`;
  const modalities = settings?.models?.[model.model_key]?.capabilities?.modalities;
  if (!modalities) return base;
  const labels = { image: "图片", audio: "音频", video: "视频" };
  const describe = (items) => items
    .filter((item) => item !== "text")
    .map((item) => labels[item] || item)
    .join("/");
  const inputs = describe(modalities.input || []);
  const outputs = describe((modalities.output || []).filter((item) => item !== "embedding"));
  const parts = [];
  if (inputs) parts.push(`输入:${inputs}`);
  if (outputs) parts.push(`输出:${outputs}`);
  return parts.length ? `${base} · ${parts.join(" · ")}` : base;
}

function formatContextWindow(value) {
  return Number.isFinite(value) ? `${value} tokens` : "unset";
}

function compressionTriggerLabel(contextWindow, triggerRatio) {
  return Number.isFinite(contextWindow) && Number.isFinite(triggerRatio)
    ? `${Math.floor(contextWindow * triggerRatio)} tokens`
    : "disabled";
}

function cleanContent(content) {
  const match = content.match(/<user-message>([\s\S]*?)<\/user-message>/);
  return (match ? match[1] : content).trim();
}

function messageDisplayRole(message) {
  return message?.kind === "lara.automation.trigger" || message?.data?.origin === "automation" ? "automation" : "user";
}

function displayMessageContent(message) {
  if (messageDisplayRole(message) === "automation") {
    const raw = message?.data?.raw_content;
    if (typeof raw === "string" && raw.trim()) return raw.trim();
    const match = String(message?.content || "").match(/<instructions>\s*([\s\S]*?)\s*<\/instructions>/);
    return (match ? match[1] : message?.content || "").trim();
  }
  return cleanContent(String(message?.content || ""));
}

function cleanSessionTitle(title) {
  return String(title || "").replace(/\s+/g, " ").trim();
}

function toolCallsFromMessage(message) {
  const payload = message?.payload && typeof message.payload === "object" ? message.payload : {};
  const rawToolCalls = message?.tool_calls || payload.tool_calls || [];
  return Array.isArray(rawToolCalls) ? rawToolCalls.filter((toolCall) => toolCall && typeof toolCall === "object") : [];
}

function toolResultActivity(message, parsed = parseJsonObject(message?.content)) {
  const payload = message?.payload && typeof message.payload === "object" ? message.payload : {};
  const status = String(parsed.status || parsed.framework_status || "result");
  const toolCallId = String(message?.model_tool_call_id || message?.tool_call_id || payload.tool_call_id || parsed.tool_call_id || "tool");
  let detail = parsed.preview || (Object.prototype.hasOwnProperty.call(parsed, "result") ? parsed.result : message?.content || "");
  if (parsed.error) {
    detail = parsed.error;
  }
  const tone = ["error", "failed", "cancelled", "rejected"].includes(status) || parsed.error
    ? "error" : ["running", "detached"].includes(status) || parsed.framework_status === "detached" ? "running" : "success";
  const taskId = parsed.task?.task_id;
  const content = stringifyDetail(detail);
  return {
    title: `工具结果：${toolCallId}`,
    toolCallId,
    content: tone === "running" && taskId ? `${content}${content ? "\n" : ""}任务：${taskId}` : content,
    status: tone,
    backgroundRunning: tone === "running",
    toolName: parsed.tool_name,
    resultRef: parsed.result_ref,
    resultSize: parsed.result_size,
    truncated: parsed.truncated,
  };
}

// Persisted and runtime tool messages carry a `results` array; map each entry
// onto the activity shape a live tool.result event produces so reloaded turns
// match the streamed view call for call.
function toolMessageResultActivities(message) {
  const results = conversationToolMessageResults(message);
  if (!results.length) {
    return [toolResultActivity(message)];
  }
  return results.map((result) => {
    const output = result.output;
    const parsed = {
      status: result.status,
      tool_name: result.name,
      ...(result.error ? { error: result.error } : {}),
      ...(result.task ? { task: result.task } : {}),
    };
    let content = "";
    if (typeof output === "string") {
      content = output;
    } else if (output && typeof output === "object") {
      if (typeof output.preview === "string" && output.preview) {
        parsed.preview = output.preview;
      }
      content = output;
    }
    return toolResultActivity(
      { content, tool_call_id: result.call_id, model_tool_call_id: result.call_id },
      parsed,
    );
  });
}

function toolCallState(toolCall) {
  const id = toolCallId(toolCall);
  const argumentsText = toolCallArguments(toolCall);
  return {
    id,
    name: toolCallName(toolCall),
    description: toolCallDescription(argumentsText),
    arguments: argumentsText,
    result: "",
    resultRef: "",
    resultSize: 0,
    truncated: false,
    status: "running",
  };
}

function mergeToolCalls(current, nextCalls) {
  const merged = Array.isArray(current) ? [...current] : [];
  const safeNextCalls = Array.isArray(nextCalls) ? nextCalls : [];
  for (const call of safeNextCalls) {
    const index = call.id ? merged.findIndex((item) => item.id === call.id) : -1;
    if (index >= 0) {
      merged[index] = {
        ...merged[index],
        ...call,
        result: merged[index].result || call.result || "",
        resultRef: merged[index].resultRef || call.resultRef || "",
      };
    } else {
      merged.push(call);
    }
  }
  return merged;
}

function applyToolResult(current, result) {
  const merged = Array.isArray(current) ? [...current] : [];
  const index = result.toolCallId ? merged.findIndex((item) => item.id === result.toolCallId) : -1;
  const patch = {
    result: result.content,
    resultRef: result.resultRef || "",
    resultSize: result.resultSize || 0,
    truncated: Boolean(result.truncated),
    status: result.status,
    backgroundRunning: Boolean(result.backgroundRunning),
  };
  if (result.toolName) {
    patch.name = result.toolName;
  }
  if (index >= 0) {
    merged[index] = { ...merged[index], ...patch };
    return merged;
  }
  const fallbackIndex = result.toolCallId ? -1 : findLastRunningToolCallIndex(merged);
  if (fallbackIndex >= 0) {
    merged[fallbackIndex] = { ...merged[fallbackIndex], ...patch };
    return merged;
  }
  if (result.toolCallId) {
    merged.push({
      id: result.toolCallId,
      name: result.toolName || "tool",
      description: "",
      arguments: "",
      ...patch,
    });
  }
  return merged;
}

function findLastRunningToolCallIndex(toolCalls) {
  const safeToolCalls = Array.isArray(toolCalls) ? toolCalls : [];
  for (let index = safeToolCalls.length - 1; index >= 0; index -= 1) {
    if (safeToolCalls[index]?.status === "running") {
      return index;
    }
  }
  return -1;
}

function toolActionLabel(name) {
  const normalized = String(name || "").trim().toLowerCase().replace(/-/g, "_");
  const aliases = {
    read: "Read",
    write: "Write",
    edit: "Edit",
    bash: "Bash",
    shell: "Bash",
    grep: "Grep",
    glob: "Glob",
    delete: "Delete",
  };
  if (aliases[normalized]) {
    return aliases[normalized];
  }
  return normalized ? normalized.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()) : "工具";
}

function toolCallDisplayLabel(call) {
  const action = toolActionLabel(call.name);
  const target = shortToolTarget(call.name, call.arguments);
  return target ? `${action} ${target}` : action;
}

function toolStatusLabel(status) {
  const kind = statusKind(status);
  if (kind === "error") {
    return "错误";
  }
  if (kind === "success") {
    return "完成";
  }
  return "运行中";
}

function shortToolTarget(name, args) {
  const parsed = parseJsonObject(args);
  const normalized = String(name || "").trim().toLowerCase().replace(/-/g, "_");
  if (normalized === "bash" || normalized === "shell") {
    return compactText(String(parsed.command || parsed.cmd || ""));
  }
  if (normalized === "grep") {
    return compactText(String(parsed.pattern || parsed.query || parsed.regex || "").trim());
  }
  if (normalized === "glob") {
    return compactText(String(parsed.pattern || parsed.glob_pattern || parsed.glob || parsed.include || "").trim());
  }
  const path = String(parsed.path || parsed.file_path || parsed.file || parsed.target || parsed.filename || "").trim();
  return path ? shortPathLabel(path) : "";
}

function compactText(value) {
  const compact = String(value || "").replace(/\s+/g, " ").trim();
  return compact.length <= 48 ? compact : `${compact.slice(0, 45)}...`;
}

function shortPathLabel(value) {
  const parts = String(value || "").replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length >= 2 ? parts.slice(-2).join("/") : parts[0] || value;
}

function toolCallId(toolCall) {
  return String(toolCall?.id || toolCall?.tool_call_id || toolCall?.call_id || "");
}

function toolCallName(toolCall) {
  const fn = toolCall?.function && typeof toolCall.function === "object" ? toolCall.function : {};
  return String(fn.name || toolCall?.name || toolCall?.tool_name || "tool");
}

function toolCallArguments(toolCall) {
  const fn = toolCall?.function && typeof toolCall.function === "object" ? toolCall.function : {};
  const args = Object.prototype.hasOwnProperty.call(fn, "arguments") ? fn.arguments : toolCall?.arguments;
  if (args === undefined || args === null) {
    return "";
  }
  return limitText(typeof args === "string" ? args : safeJsonStringify(args), TOOL_ARGUMENT_PREVIEW_LIMIT);
}

function toolCallDescription(argumentsText) {
  const parsed = parseJsonObject(argumentsText);
  return String(parsed.description || "").trim();
}

function parseJsonObject(value) {
  try {
    const parsed = JSON.parse(String(value || ""));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function stringifyDetail(value) {
  return limitText(typeof value === "string" ? value : safeJsonStringify(value), TOOL_RESULT_PREVIEW_LIMIT);
}

function stringifyFullDetail(value) {
  return typeof value === "string" ? value : safeJsonStringify(value);
}

function activityTone(status) {
  const kind = statusKind(status);
  if (kind === "error") {
    return "error";
  }
  if (kind === "running") {
    return "warning";
  }
  if (kind === "success") {
    return "success";
  }
  return "ready";
}

function activityToolBatchTitle(calls) {
  const safeCalls = Array.isArray(calls) ? calls : [];
  for (const call of safeCalls) {
    if (call.description) {
      return call.description;
    }
  }
  return "Acting";
}

export function eventSummary(event) {
  const payload = event.payload || {};
  if (event.type === "conversation.tool_message") {
    const results = conversationToolMessageResults(payload);
    if (results.length) {
      return results
        .map((result) => `${String(result.name || "tool")}  ${conversationToolResultStatus(result)}`)
        .join("  ·  ");
    }
    return "No tool results";
  }
  if (payload.trace_tool) {
    const status = String(payload.status || "running");
    const parts = [status === "error" ? "失败" : status === "success" ? "已完成" : "运行中"];
    const target = shortToolTarget(payload.tool_name, payload.arguments);
    if (target) {
      parts.push(target);
    }
    return parts.join("  ");
  }
  if (isFileTraceEvent(event)) {
    return fileEventPath(event) || "Path unavailable";
  }
  if (payload.delta) {
    return payload.delta;
  }
  if (payload.error) {
    const diagnostic = modelFailureDiagnostic(payload.model_failure);
    return diagnostic ? `${payload.error}  ·  ${diagnostic}` : payload.error;
  }
  if (payload.content) {
    return String(payload.content).slice(0, 180);
  }
  return limitText(safeJsonStringify(payload), 180);
}

function modelFailureDiagnostic(failure) {
  const attempts = Array.isArray(failure?.details?.attempts) ? failure.details.attempts : [];
  const attempt = attempts.at(-1);
  if (!attempt || typeof attempt !== "object") {
    return "";
  }
  const parts = [];
  if (attempt.reason_code) parts.push(String(attempt.reason_code));
  if (attempt.http_status) parts.push(`HTTP ${attempt.http_status}`);
  if (attempt.model_key) parts.push(String(attempt.model_key));
  return parts.join("  ·  ");
}

export function eventTitle(event) {
  const payload = event.payload || {};
  if (payload.trace_tool) {
    return toolActionLabel(payload.tool_name);
  }
  if (isFileTraceEvent(event)) {
    return fileEventAction(event.type);
  }
  return event.type || "event";
}

export function traceEventDetails(event, tab) {
  const payload = event.payload || {};
  if (tab === "Tools" && payload.trace_tool) {
    const sections = [];
    if (payload.arguments) {
      sections.push(`Arguments\n${payload.arguments}`);
    }
    if (payload.result) {
      sections.push(`${payload.status === "error" ? "Error" : "Result"}\n${payload.result}`);
    }
    return sections.join("\n\n");
  }
  if (tab === "Changes" && isFileTraceEvent(event)) {
    return safeJsonStringify(payload);
  }
  if (tab === "Events") {
    if (event.type === "conversation.tool_message") {
      return conversationToolMessageDetails(payload);
    }
    const content = typeof payload.content === "string" ? payload.content : "";
    return content || safeJsonStringify(payload);
  }
  return "";
}

function conversationToolMessageResults(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return results.filter((result) => result && typeof result === "object");
}

function conversationToolResultStatus(result) {
  return String(result?.status || (result?.error ? "error" : "result"));
}

function conversationToolResultValue(result) {
  return result?.error || result?.output || "";
}

function conversationToolMessageDetails(payload) {
  const results = conversationToolMessageResults(payload);
  if (!results.length) {
    return "No tool results";
  }
  return results.map((result) => {
    const name = String(result.name || "tool");
    const status = conversationToolResultStatus(result);
    const callId = String(result.call_id || "");
    const heading = [name, status, callId].filter(Boolean).join("  ·  ");
    return `${heading}\n${stringifyDetail(conversationToolResultValue(result))}`;
  }).join("\n\n");
}

function isFileTraceEvent(event) {
  const type = String(event?.type || "");
  return type.startsWith("file.") || type === "diff.created";
}

function fileEventPath(event) {
  const payload = event?.payload || {};
  return String(payload.path || payload.file_path || payload.file || payload.target || "").trim();
}

function fileEventAction(type) {
  const actions = {
    "file.read": "Read file",
    "file.write": "Wrote file",
    "file.edit": "Edited file",
    "file.delete": "Deleted file",
    "diff.created": "Created diff",
  };
  return actions[String(type || "")] || String(type || "File event");
}

function eventTone(event) {
  const payload = event.payload || {};
  if (payload.trace_tool) {
    return activityTone(payload.status);
  }
  if (payload.error || payload.status === "error") {
    return "error";
  }
  if (String(event.type || "").includes("warning")) {
    return "warning";
  }
  return "success";
}

function statusTone(status) {
  const kind = statusKind(status);
  if (kind === "running") {
    return "warning";
  }
  if (kind === "error") {
    return "error";
  }
  return "success";
}

function statusLabel(status) {
  const value = String(status || "Ready");
  if (value === "passed") {
    return "完成";
  }
  const labels = {
    Ready: "就绪",
    Connecting: "连接中",
    Loading: "加载中",
    Running: "运行中",
    Reconnecting: "重连中",
    Done: "完成",
    Error: "错误",
    Skipped: "已跳过",
    Offline: "离线",
  };
  return labels[value] || value;
}

function sessionStatusLabel(status) {
  const kind = sessionStatusKind(status);
  if (kind === "running") {
    return "Running";
  }
  if (kind === "error") {
    return "Error";
  }
  return "Done";
}

function sessionStatusKind(status) {
  const kind = statusKind(status);
  return kind === "running" || kind === "error" ? kind : "success";
}

function statusKind(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("error") || value.includes("fail")) return "error";
  if (value.includes("run") || value.includes("live") || value.includes("load")) return "running";
  if (value.includes("success") || value.includes("done") || value.includes("pass")) return "success";
  return "ready";
}

function shortPath(path) {
  if (!path) {
    return "";
  }
  return path.length <= 36 ? path : `...${path.slice(-33)}`;
}

function shortId(value) {
  if (!value) {
    return "";
  }
  return value.length <= 16 ? value : `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function readableError(err) {
  return err instanceof Error ? err.message : String(err);
}

function stringifyToolArgs(value) {
  if (value === undefined || value === null) {
    return "";
  }
  return limitText(typeof value === "string" ? value : safeJsonStringify(value), TOOL_ARGUMENT_PREVIEW_LIMIT);
}

function safeJsonStringify(value) {
  try {
    return JSON.stringify(value, (_key, item) => (typeof item === "bigint" ? item.toString() : item), 2);
  } catch {
    return String(value);
  }
}

function limitText(value, limit) {
  const text = String(value || "");
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit)}\n\n[truncated ${text.length - limit} chars]`;
}
