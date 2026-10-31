import { useCallback, useEffect, useRef, useState } from "react";

export function copyContextValue(value) {
  globalThis.navigator?.clipboard?.writeText?.(value).catch(() => {});
}

// Shared "已复制" flash so every copy button reverts without leaking timers.
export function useCopiedFeedback() {
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
