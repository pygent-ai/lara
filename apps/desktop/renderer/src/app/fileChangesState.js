// Pure helpers for the per-turn file change summaries. The backend groups
// persisted diff records by (case_run_id, turn_id); these helpers only match
// turns to chat bubbles and fold turn summaries into display counts.

export function turnChangesForMessage(turns, message) {
  if (!Array.isArray(turns) || !turns.length) return [];
  const turnIds = Array.isArray(message?.turnIds) ? message.turnIds : [];
  const caseRunId = String(message?.caseRunId || "");
  return turns.filter((turn) => {
    if (turnIds.length) {
      return turnIds.includes(String(turn.turn_id || ""));
    }
    // A bubble without turn ids (live stream before the history reload)
    // falls back to the whole run so the overview still lands on it.
    return Boolean(caseRunId) && String(turn.case_run_id || "") === caseRunId;
  });
}

export function summarizeTurns(turns) {
  const files = [];
  const seen = new Map();
  let changeCount = 0;
  let additionsKnown = true;
  let deletionsKnown = true;
  let additions = 0;
  let deletions = 0;
  for (const turn of Array.isArray(turns) ? turns : []) {
    changeCount += Number(turn.change_count) || 0;
    if (turn.total_additions == null) additionsKnown = false;
    else additions += Number(turn.total_additions) || 0;
    if (turn.total_deletions == null) deletionsKnown = false;
    else deletions += Number(turn.total_deletions) || 0;
    for (const file of Array.isArray(turn.files) ? turn.files : []) {
      const key = String(file.path || "");
      const previous = seen.get(key);
      if (previous) {
        // Later turns own the final label; counts accumulate across turns.
        previous.changeType = String(file.change_type || previous.changeType);
        previous.diffId = String(file.diff_id || previous.diffId);
        previous.patchAvailable = Boolean(file.patch_available) || previous.patchAvailable;
        previous.additions = addKnown(previous.additions, file.additions);
        previous.deletions = addKnown(previous.deletions, file.deletions);
        previous.changes += Number(file.changes) || 1;
        continue;
      }
      const entry = {
        path: key,
        changeType: String(file.change_type || "modified"),
        additions: normalizeCount(file.additions),
        deletions: normalizeCount(file.deletions),
        changes: Number(file.changes) || 1,
        diffId: String(file.diff_id || ""),
        patchAvailable: Boolean(file.patch_available),
      };
      seen.set(key, entry);
      files.push(entry);
    }
  }
  return {
    files,
    changeCount,
    fileCount: files.length,
    additions: additionsKnown ? additions : null,
    deletions: deletionsKnown ? deletions : null,
  };
}

function addKnown(left, right) {
  if (left == null || right == null) return null;
  return left + (Number(right) || 0);
}

function normalizeCount(value) {
  return value == null ? null : Number(value) || 0;
}

// Live strip: fold every turn of the running task into one headline count.
export function summarizeRunTurns(turns, caseRunId) {
  if (!caseRunId) return null;
  const runTurns = (Array.isArray(turns) ? turns : []).filter(
    (turn) => String(turn.case_run_id || "") === String(caseRunId),
  );
  if (!runTurns.length) return null;
  return summarizeTurns(runTurns);
}

export const CHANGE_TYPE_LABELS = {
  created: "新增",
  modified: "修改",
  deleted: "删除",
};

export function changeTypeLabel(changeType) {
  return CHANGE_TYPE_LABELS[String(changeType || "")] || "修改";
}

// Single-character badge: A/M/D matches the git status shorthand.
export function changeTypeBadge(changeType) {
  const labels = { created: "A", modified: "M", deleted: "D" };
  return labels[String(changeType || "")] || "M";
}
