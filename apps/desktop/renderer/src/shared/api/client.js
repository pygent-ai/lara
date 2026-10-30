const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
const STREAM_RESUME_TIMEOUT_MS = 55_000;
const STREAM_RESUME_RETRY_DELAY_MS = 1_000;
// The backend emits a keep-alive at least every 10s while a stream is open
// (CHAT_KEEPALIVE_SECONDS), so total silence means the connection or the
// server-side resolve is wedged. Without this bound a pending fetch would
// spin the composer forever: the resume window below only starts counting
// after an attempt fails, and a hung request never fails on its own.
const STREAM_STALL_TIMEOUT_MS = 30_000;

export function createApiClient(options = {}) {
  const baseUrl = normalizeBaseUrl(options.baseUrl || defaultBaseUrl());
  const fetchImpl = options.fetchImpl || globalThis.fetch?.bind(globalThis);
  if (typeof fetchImpl !== "function") {
    throw new Error("Fetch API is not available");
  }

  // Polled sidebar list: remember the ETag so unchanged payloads come back
  // as 304 and the renderer keeps its current state without any work.
  let sessionGroupsEtag = "";

  async function jsonRequest(path, { method = "GET", body, headers, signal, onResponse } = {}) {
    const response = await fetchImpl(`${baseUrl}${path}`, {
      method,
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    onResponse?.(response);
    if (response.status === 304) {
      return null;
    }
    if (!response.ok) {
      const error = new Error(await responseErrorText(response));
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  return {
    baseUrl,
    getHealth: (options = {}) => jsonRequest("/health", options),
    uploadAttachment: async ({ file, scopeId } = {}) => {
      if (typeof file === "undefined" || file === null) {
        throw new Error("uploadAttachment requires a file");
      }
      const form = new FormData();
      form.append("file", file, file.name || "clipboard");
      if (scopeId) form.append("scope_id", scopeId);
      const response = await fetchImpl(`${baseUrl}/uploads`, {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const error = new Error(await responseErrorText(response));
        error.status = response.status;
        throw error;
      }
      return response.json();
    },
    // Direct <img src> URL for stored-upload previews (thumbnails/icons).
    uploadContentUrl: (scopeId, path) =>
      `${baseUrl}/uploads/content?scope_id=${encodeURIComponent(scopeId || "")}&path=${encodeURIComponent(path)}`,
    deleteUpload: (scopeId, path, options = {}) =>
      jsonRequest(`/uploads?scope_id=${encodeURIComponent(scopeId || "")}&path=${encodeURIComponent(path)}`, {
        ...options,
        method: "DELETE",
      }),
    getSettings: (options = {}) => jsonRequest("/settings", options),
    getModelCatalogs: (options = {}) => jsonRequest("/settings/model-catalogs", options),
    discoverModels: (request, options = {}) =>
      jsonRequest("/settings/models/discover", {
        ...options, method: "POST", body: request,
      }),
    updateSettings: (settings, { modelKeyRenames, ...options } = {}) =>
      jsonRequest("/settings", {
        ...options,
        method: "PATCH",
        body: settingsPayload(settings, modelKeyRenames),
      }),
    listProjects: (options = {}) => jsonRequest("/projects", options),
    listAutomations: (status = "", options = {}) =>
      jsonRequest(`/automations${status ? `?status=${encodeURIComponent(status)}` : ""}`, options),
    createAutomation: (request, options = {}) => jsonRequest("/automations", {
      ...options, method: "POST", body: request,
    }),
    updateAutomation: (automationId, request, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}`, {
        ...options, method: "PATCH", body: request,
      }),
    deleteAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}`, {
        ...options, method: "DELETE",
      }),
    pauseAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/pause`, {
        ...options, method: "POST",
      }),
    resumeAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/resume`, {
        ...options, method: "POST",
      }),
    runAutomation: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/run`, {
        ...options, method: "POST",
      }),
    listAutomationRuns: (automationId, options = {}) =>
      jsonRequest(`/automations/${encodeURIComponent(automationId)}/runs`, options),
    cancelAutomationRun: (automationId, runId, options = {}) =>
      jsonRequest(
        `/automations/${encodeURIComponent(automationId)}/runs/${encodeURIComponent(runId)}/cancel`,
        { ...options, method: "POST" },
      ),
    removeProject: (scopeId, options = {}) =>
      jsonRequest(`/projects?scope_id=${encodeURIComponent(scopeId)}`, {
        ...options,
        method: "DELETE",
      }),
    listWorkspaceEntries: (scopeId, path = "", options = {}) =>
      jsonRequest(`/workspace/entries?scope_id=${encodeURIComponent(scopeId)}&path=${encodeURIComponent(path)}`, options),
    readWorkspaceFile: (scopeId, path, options = {}) =>
      jsonRequest(`/workspace/file?scope_id=${encodeURIComponent(scopeId)}&path=${encodeURIComponent(path)}`, options),
    executeTerminalCommand: (scopeId, command, options = {}) =>
      jsonRequest("/terminal/execute", {
        ...options,
        method: "POST",
        body: { scope_id: scopeId, command },
      }),
    resetTerminal: (scopeId, options = {}) =>
      jsonRequest("/terminal/reset", {
        ...options,
        method: "POST",
        body: { scope_id: scopeId },
      }),
    listSessions: (options = {}) => jsonRequest("/sessions", options),
    listSessionGroups: (options = {}) => jsonRequest("/sessions/groups", {
      ...options,
      headers: {
        ...(sessionGroupsEtag ? { "If-None-Match": sessionGroupsEtag } : {}),
        ...options.headers,
      },
      onResponse: (response) => {
        sessionGroupsEtag = response.headers.get("etag") || "";
      },
    }),
    updateProjectOrder: (orderedScopeIds, options = {}) =>
      jsonRequest("/projects/order", {
        ...options,
        method: "PATCH",
        body: { ordered_scope_ids: orderedScopeIds },
      }),
    updateSessionOrder: (scopeId, orderedSessionIds, options = {}) =>
      jsonRequest("/sessions/groups/order", {
        ...options,
        method: "PATCH",
        body: { scope_id: scopeId, ordered_session_ids: orderedSessionIds },
      }),
    updateSessionPin: (sessionId, { pinned, scopeId, ...options } = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}/pin`, {
        ...options,
        method: "PATCH",
        body: { pinned, scope_id: scopeId || undefined },
      }),
    createSession: (request = {}, options = {}) =>
      jsonRequest("/sessions", {
        ...options,
        method: "POST",
        body: {
          case_id: request.caseId || "chat",
          mode: request.mode || "chat",
          scope_id: request.scopeId || undefined,
          model_group_name: request.modelGroupName || undefined,
        },
      }),
    getSession: (sessionId, { scopeId, historyLimit, historyBeforeSequence, ...options } = {}) =>
      jsonRequest(
        `/sessions/${encodeURIComponent(sessionId)}${queryString({
          scope_id: scopeId,
          history_limit: historyLimit,
          history_before_sequence: historyBeforeSequence,
        })}`,
        options,
      ),
    deleteSession: (sessionId, { scopeId, ...options } = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}${scopeQuery(scopeId)}`, {
        ...options,
        method: "DELETE",
      }),
    forkSession: (sessionId, { scopeId, atSequence, ...options } = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}/fork${scopeQuery(scopeId)}`, {
        ...options,
        method: "POST",
        body: { at_sequence: atSequence || undefined },
      }),
    getTraceEvents: (sessionId, caseRunId, { eventLimit, contextSnapshotLimit, ...options } = {}) =>
      jsonRequest(
        `/traces/${encodeURIComponent(sessionId)}/${encodeURIComponent(caseRunId)}${queryString({
          event_limit: eventLimit,
          context_snapshot_limit: contextSnapshotLimit,
        })}`,
        options,
      ),
    getSessionActivity: (sessionId, { eventLimit, ...options } = {}) =>
      jsonRequest(
        `/traces/${encodeURIComponent(sessionId)}/activity${queryString({
          event_limit: eventLimit,
        })}`,
        options,
      ),
    getToolResult: (toolCallId, options = {}) =>
      jsonRequest(`/tool-results/${encodeURIComponent(toolCallId)}`, options),
    deliverApproval: (approvalId, approved, comment = "", options = {}) =>
      jsonRequest(`/chat/approvals/${encodeURIComponent(approvalId)}`, {
        ...options,
        method: "POST",
        body: { approved, comment },
      }),
    steerChat: (executionId, { sessionId, inputId, message, attachments }, options = {}) =>
      jsonRequest(`/chat/executions/${encodeURIComponent(executionId)}/steering`, {
        ...options, method: "POST",
        body: {
          session_id: sessionId,
          input_id: inputId,
          message: message || null,
          ...(Array.isArray(attachments) && attachments.length ? { attachments } : {}),
        },
      }),
    cancelChatTurn: (executionId, { sessionId }, options = {}) =>
      jsonRequest(`/chat/executions/${encodeURIComponent(executionId)}/cancel`, {
        ...options, method: "POST",
        body: { session_id: sessionId },
      }),
    updateSessionModel: (sessionId, selectedModelKey, { scopeId, ...options } = {}) =>
      jsonRequest(`/sessions/${encodeURIComponent(sessionId)}/model${scopeQuery(scopeId)}`, {
        ...options,
        method: "PATCH",
        body: { selected_model_key: selectedModelKey },
      }),
    streamChat: (request, handlers = {}) =>
      streamChatTurn({
        baseUrl,
        fetchImpl,
        request,
        onEvent: handlers.onEvent,
        onConnectionState: handlers.onConnectionState,
        signal: handlers.signal,
      }),
  };
}

export function settingsPayload(settings, modelKeyRenames) {
  const contextWindow = settingsNumber(settings.contextWindow);
  return compactObject({
    workspace_root: settingsString(settings.workspaceRoot),
    agent_alias: settingsString(settings.agent),
    max_steps: Number.isFinite(settings.maxSteps) ? settings.maxSteps : undefined,
    context_window: Object.hasOwn(settings, "contextWindow") ? contextWindow ?? null : undefined,
    approvals_enabled: typeof settings.approvalsEnabled === "boolean" ? settings.approvalsEnabled : undefined,
    model_config: nativeModelConfigPayload(settings),
    model_key_renames: modelKeyRenamesPayload(modelKeyRenames),
    default_model_group: cleanString(settings.defaultModelGroup),
    credential_values: credentialValuesPayload(settings.credentialValues),
    retry: settings.retry ? {
      max_attempts_per_model: settingsNumber(settings.retry.max_attempts_per_model),
      attempt_idle_timeout_seconds: settingsNumber(settings.retry.attempt_idle_timeout_seconds),
      backoff_initial: settingsNonNegativeNumber(settings.retry.backoff_initial),
      backoff_maximum: settingsNonNegativeNumber(settings.retry.backoff_maximum),
      backoff_multiplier: settingsNumber(settings.retry.backoff_multiplier),
    } : undefined,
  });
}

function modelKeyRenamesPayload(renames) {
  if (!renames || typeof renames !== "object") return undefined;
  const entries = Object.entries(renames).filter(([previous, current]) => cleanString(previous) && cleanString(current));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function nativeModelConfigPayload(settings) {
  if (!settings.connections || !settings.models || !settings.modelGroups) {
    return undefined;
  }
  return {
    connections: Object.fromEntries(Object.entries(settings.connections).map(([key, connection]) => {
      const { credential_source: _source, ...nativeConnection } = connection;
      return [key, nativeConnection];
    })),
    models: settings.models,
    model_groups: settings.modelGroups,
  };
}

function credentialValuesPayload(values) {
  if (!values || typeof values !== "object") return undefined;
  const entries = Object.entries(values).filter(([, value]) => cleanString(value));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

export function parseSseEvents(text) {
  const events = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const event = parseSseBlock(block);
    if (event !== null) {
      events.push(event);
    }
  }
  return events;
}

async function streamChatTurn({ baseUrl, fetchImpl, request, onEvent, onConnectionState, signal }) {
  let executionId = request.executionId || null;
  let afterSequence = Number.isFinite(request.afterSequence) ? request.afterSequence : null;
  let maxSequenceSeen = Number.isFinite(request.afterSequence) ? request.afterSequence : -1;
  let disconnectedAt = null;
  let terminal = false;
  let attempt = 0;

  while (true) {
    // Each attempt is bounded by event activity, not just by failures: a
    // silent connection must not hold the stream open indefinitely.
    const stallController = new AbortController();
    const onOuterAbort = () => stallController.abort();
    if (signal) {
      if (signal.aborted) stallController.abort();
      else signal.addEventListener("abort", onOuterAbort, { once: true });
    }
    let stallTimer = null;
    let stalled = false;
    const armStallTimer = () => {
      if (stallTimer !== null) clearTimeout(stallTimer);
      stalled = false;
      stallTimer = setTimeout(() => {
        stalled = true;
        stallController.abort();
      }, STREAM_STALL_TIMEOUT_MS);
    };
    try {
      armStallTimer();
      await streamChatAttempt({
        baseUrl,
        fetchImpl,
        request: { ...request, executionId, afterSequence },
        onActivity: armStallTimer,
        onEvent: (event) => {
          armStallTimer();
          const data = event?.data || {};
          if (Number.isFinite(data.sequence) && data.sequence > maxSequenceSeen) {
            maxSequenceSeen = data.sequence;
            // Only genuinely new events prove progress. A reconnect that
            // merely replays already-seen events must not reopen the give-up
            // window below, or a server that dies right after replaying keeps
            // the client reconnecting forever.
            disconnectedAt = null;
            attempt = 0;
          }
          onConnectionState?.("connected");
          if (data.execution_id) {
            executionId = data.execution_id;
          }
          if (Number.isFinite(data.sequence)) {
            afterSequence = Math.max(afterSequence ?? -1, data.sequence);
          }
          terminal ||= ["execution.completed", "execution.failed", "execution.cancelled", "execution.deadline_exceeded", "lara.transport.error"].includes(data.kind);
          emitStreamEvent(event, onEvent);
        },
        signal: stallController.signal,
      });
      if (terminal) return;
      throw new Error("Execution stream ended before the task finished");
    } catch (err) {
      if (terminal) return;
      if (signal?.aborted) {
        throw err;
      }
      if (isAbortError(err)) {
        if (!stalled) throw err;
        err = new Error("Execution stream stalled: no events, including keep-alives, arrived within 30s");
      }
      if (!executionId) {
        throw err;
      }
      disconnectedAt ??= Date.now();
      if (Date.now() - disconnectedAt >= STREAM_RESUME_TIMEOUT_MS) {
        throw err;
      }
      onConnectionState?.("reconnecting");
      attempt += 1;
      await delay(Math.min(STREAM_RESUME_RETRY_DELAY_MS * attempt, 5_000), signal);
    } finally {
      if (stallTimer !== null) clearTimeout(stallTimer);
      if (signal) signal.removeEventListener("abort", onOuterAbort);
    }
  }
}

async function streamChatAttempt({ baseUrl, fetchImpl, request, onEvent, onActivity, signal }) {
  const response = await fetchImpl(`${baseUrl}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: request.executionId || !request.message ? null : request.message,
      session_id: request.sessionId || null,
      scope_id: request.scopeId || null,
      case_id: request.caseId || "chat",
      model_group_name: request.modelGroupName || null,
      turn_id: request.turnId || null,
      execution_id: request.executionId || null,
      after_sequence: Number.isFinite(request.afterSequence) ? request.afterSequence : null,
      ...(Array.isArray(request.attachments) && request.attachments.length
        ? { attachments: request.attachments }
        : {}),
    }),
    signal,
  });
  if (!response.ok) {
    throw new Error(await responseErrorText(response));
  }
  if (!response.body?.getReader) {
    const text = await response.text();
    for (const event of parseSseEvents(text)) {
      emitStreamEvent(event, onEvent);
    }
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    // Any bytes from the wire count as liveness, including keep-alive
    // comment lines that never surface as parsed events. Without this the
    // caller's stall timer fires on healthy streams whose turn is merely
    // quiet (for example while the model is generating).
    onActivity?.();
    buffer += decoder.decode(value, { stream: true });
    const nextBuffer = emitCompleteSseBlocks(buffer, onEvent);
    buffer = nextBuffer;
  }
  buffer += decoder.decode();
  emitCompleteSseBlocks(`${buffer}\n\n`, onEvent);
}

function delay(ms, signal) {
  if (signal?.aborted) {
    return Promise.reject(abortError());
  }
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timeout);
        reject(abortError());
      },
      { once: true },
    );
  });
}

function isAbortError(err) {
  return err instanceof DOMException && err.name === "AbortError";
}

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

function emitCompleteSseBlocks(buffer, onEvent) {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const pending = blocks.pop() || "";
  for (const block of blocks) {
    const event = parseSseBlock(block);
    if (event !== null) {
      emitStreamEvent(event, onEvent);
    }
  }
  return pending;
}

function emitStreamEvent(event, onEvent) {
  if (!onEvent) {
    return;
  }
  try {
    onEvent(event);
  } catch (err) {
    console.error("SSE event handler failed", err, event);
  }
}

function parseSseBlock(block) {
  const lines = block.split(/\r?\n/);
  let eventName = "message";
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const index = line.indexOf(":");
    const field = index === -1 ? line : line.slice(0, index);
    const rawValue = index === -1 ? "" : line.slice(index + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    if (field === "event") {
      eventName = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const dataText = dataLines.join("\n");
  return {
    event: eventName,
    data: JSON.parse(dataText),
  };
}

function defaultBaseUrl() {
  const windowBaseUrl = globalThis.window?.__LARA_API_BASE_URL__;
  if (typeof windowBaseUrl === "string" && windowBaseUrl.trim()) {
    return windowBaseUrl;
  }
  const viteBaseUrl = import.meta.env?.VITE_LARA_API_BASE_URL;
  return typeof viteBaseUrl === "string" && viteBaseUrl.trim() ? viteBaseUrl : DEFAULT_BASE_URL;
}

function normalizeBaseUrl(value) {
  return value.replace(/\/+$/, "");
}

function scopeQuery(scopeId) {
  return scopeId ? `?scope_id=${encodeURIComponent(scopeId)}` : "";
}

function queryString(params) {
  const entries = Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (!entries.length) return "";
  return `?${entries.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join("&")}`;
}

function settingsValue(value) {
  return typeof value === "string" ? value.trim() : value;
}

function settingsString(value) {
  const clean = settingsValue(value);
  return typeof clean === "string" ? clean : undefined;
}

function settingsNumber(value) {
  if (value === null || value === undefined || value === "") {
    return undefined;
  }
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number > 0 ? number : undefined;
}

function settingsNonNegativeNumber(value) {
  if (value === null || value === undefined || value === "") {
    return undefined;
  }
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) && number >= 0 ? number : undefined;
}

function cleanString(value) {
  const clean = settingsValue(value);
  return typeof clean === "string" && clean ? clean : undefined;
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}

async function responseErrorText(response) {
  const fallback = `Request failed with ${response.status}`;
  try {
    const text = await response.text();
    return text || fallback;
  } catch {
    return fallback;
  }
}
