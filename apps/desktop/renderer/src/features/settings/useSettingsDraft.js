import { useEffect, useState } from "react";
import { settingsToDraft } from "./settingsModel.js";

export function useSettingsDraft(settings, api, disabled) {
  // The page owns its data: it re-fetches once on mount so the draft and the
  // saved baseline never start from a stale app-level cache. App settings is
  // only a read-through cache for the rest of the app; saves patch it from
  // the saved response, never through this hook.
  const [snapshot, setSnapshot] = useState(settings);
  const [draft, setDraft] = useState(() => settingsToDraft(settings));
  const [catalogs, setCatalogs] = useState(null);
  useEffect(() => {
    if (!api?.getSettings) return;
    let active = true;
    api.getSettings().then((value) => { if (active) setSnapshot(value); }).catch(() => {});
    return () => { active = false; };
  }, [api]);
  useEffect(() => { setDraft(settingsToDraft(snapshot)); }, [snapshot]);
  useEffect(() => {
    if (!api?.getModelCatalogs) return;
    let active = true;
    api.getModelCatalogs().then(value => { if (active) setCatalogs(value); }).catch(() => {});
    return () => { active = false; };
  }, [api]);
  function setField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function removeConnection(connectionKey) {
    setDraft((current) => {
      if (Object.values(current.models).some((model) => model.connection === connectionKey)) return current;
      const { [connectionKey]: _removed, ...connections } = current.connections;
      return { ...current, connections };
    });
  }

  function toggleGroupModel(groupName, modelKey) {
    setDraft((current) => {
      const previous = current.modelGroups[groupName]?.models || [];
      const models = previous.includes(modelKey) ? previous.filter((item) => item !== modelKey) : [...previous, modelKey];
      return { ...current, modelGroups: { ...current.modelGroups, [groupName]: { models } } };
    });
  }

  function moveGroupModel(groupName, modelKey, offset) {
    setDraft((current) => {
      const models = [...(current.modelGroups[groupName]?.models || [])];
      const from = models.indexOf(modelKey);
      const to = from + offset;
      if (from < 0 || to < 0 || to >= models.length) return current;
      [models[from], models[to]] = [models[to], models[from]];
      return { ...current, modelGroups: { ...current.modelGroups, [groupName]: { models } } };
    });
  }

  function renameGroup(previousName, nextName) {
    setDraft((current) => {
      const name = nextName.trim();
      if (!name || name === previousName || Object.hasOwn(current.modelGroups, name)) return current;
      const modelGroups = Object.fromEntries(Object.entries(current.modelGroups).map(([key, group]) => [key === previousName ? name : key, group]));
      return { ...current, modelGroups, defaultModelGroup: current.defaultModelGroup === previousName ? name : current.defaultModelGroup };
    });
  }

  return { snapshot, api, disabled, draft, setDraft, catalogs, setField, removeConnection, toggleGroupModel, moveGroupModel, renameGroup };
}
