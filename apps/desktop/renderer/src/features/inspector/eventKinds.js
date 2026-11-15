// Classification of the runtime's execution-event kinds for the inspector.
//
// The SDK journal interleaves the events a person reads a trace for (tool
// lifecycle, model rounds, runtime messages, terminal outcomes) with
// high-frequency telemetry: streaming deltas, span boundaries, effect
// bookkeeping, per-attempt model accounting. One tool round emits dozens of
// the latter for every one of the former, so a bounded live buffer that keeps
// both is soon all telemetry. The live trace keeps only the readable kinds.

const TELEMETRY_PREFIXES = [
  "span.",
  "effect.",
];

// Attempt kinds are listed one by one: a failed attempt is diagnostic.
const TELEMETRY_KINDS = new Set([
  "model.attempt.started",
  "model.attempt.succeeded",
  "model.request.prepared",
  "model.usage",
  "model.tool_call.started",
  "tool.authorized",
  "execution.submitted",
  "execution.admitted",
  "pygent.v1.context.usage",
  "lara.transport.keepalive",
]);

export function isTelemetryEventKind(kind) {
  const value = String(kind || "");
  if (!value) return false;
  if (value.endsWith(".delta")) return true;
  if (TELEMETRY_KINDS.has(value)) return true;
  return TELEMETRY_PREFIXES.some((prefix) => value.startsWith(prefix));
}
