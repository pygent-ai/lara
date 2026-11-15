import { useCallback, useEffect, useState } from "react";

// The settings page owns its data: it fetches the current settings, the
// Pygent catalogs and the per-model session usage once on mount, so editors
// start from the persisted state rather than the app-level cache. `usage`
// backs the deletion impact notices and refreshes after every save.
export function useSettingsData(settings, api) {
  const [snapshot, setSnapshot] = useState(settings);
  const [catalogs, setCatalogs] = useState(null);
  const [usage, setUsage] = useState(null);

  useEffect(() => {
    if (!api?.getSettings) return;
    let active = true;
    api.getSettings().then((value) => { if (active) setSnapshot(value); }).catch(() => {});
    return () => { active = false; };
  }, [api]);

  useEffect(() => {
    if (!api?.getModelCatalogs) return;
    let active = true;
    api.getModelCatalogs().then((value) => { if (active) setCatalogs(value); }).catch(() => {});
    return () => { active = false; };
  }, [api]);

  const refreshUsage = useCallback(() => {
    if (!api?.getModelUsage) return;
    api.getModelUsage().then(setUsage).catch(() => {});
  }, [api]);
  useEffect(() => { refreshUsage(); }, [refreshUsage]);

  return { snapshot, catalogs, usage, refreshUsage };
}
