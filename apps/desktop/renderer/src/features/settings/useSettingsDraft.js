import { useEffect, useState } from "react";
import { settingsToDraft } from "./settingsModel.js";

export function useSettingsDraft(settings, api, disabled) {
  const [draft, setDraft] = useState(() => settingsToDraft(settings));
  const [catalogs, setCatalogs] = useState(null);
  useEffect(() => { setDraft(settingsToDraft(settings)); }, [settings]);
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

  return { settings, api, disabled, draft, setDraft, catalogs, setField, removeConnection, toggleGroupModel, moveGroupModel, renameGroup };
}
