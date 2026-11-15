# Settings feature

`SettingsPage.jsx` owns the full-workspace settings page: category navigation (模型 / 项目与 Agent / 运行与权限), the dirty-state close guard and the status footer. The page owns its data (`useSettingsData.js`): it fetches the settings, the Pygent catalogs and the per-model session usage once on mount, keeps `savedDraft` as the last persisted state, and rebases it on every save response so credential sources reflect what the server stored and typed secrets are never sent twice. The app-level `settings` state is only a read-through cache patched by `onPersist`'s return value.

There is no global save. Every entity saves itself and deletions commit immediately after an inline confirmation:

- **Connection** (`ConnectionSettings.jsx`): one editor per connection with a single 保存 covering provider, name, API key, endpoints, the connection's models and the advanced fields (credential name, proxy, TLS, test). Rows stay mounted while collapsed so folding never drops unsaved edits; a row head shows 未保存 while its editor is dirty. A new connection starts from the provider choice, takes the catalog defaults, asks only for the API key, and opens its model picker right after creation. Deleting a connection removes its models too.
- **Models** live inside their connection. `ModelPicker.jsx` fetches the provider's model list, lets the user tick several (or type an ID the provider does not list) and creates records with catalog capabilities or the agentic default. A saved model row is a one-line summary (`capabilitySummary`) that expands into model ID, local name, protocol and `ModelCapabilitiesEditor`. Renaming the local name is forwarded as `model_key_renames` so the backend migrates sessions; deleting a saved model asks first and commits at once, scrubbing it from every group.
- **Model groups** (`GroupSettings.jsx`): a list beside one editor. A group is a primary model plus ordered fallbacks; the editor saves one group, the list's star sets the default group and the trash deletes with confirmation. An untouched group name follows the primary model.
- **项目与 Agent** and **运行与权限** edit page-level fields and save their own field list; the retry policy lives under 运行与权限 because it is global.

Deletion confirmations (`ConfirmDelete.jsx`) show the backend's usage counts (`GET /settings/model-usage`) through `deletionImpact`, so the user sees how many chats move to another model or group before confirming. The server allows every deletion and rebinds affected sessions (`rebind_model_selections`).

`settingsModel.js` holds the pure model: draft conversion, factories, validation (`connectionError`, `groupError`), dirtiness and the commit functions (`commitConnection`, `commitConnectionDelete`, `commitModelDelete`, `commitGroup`, `commitGroupDelete`) that merge one editor's buffer over the saved draft. `settings.css` carries every settings style scoped under `.settings-page`; the app stylesheet has no settings rules.

Tests: `settingsModel.test.mjs` covers the pure model, `settings.render.test.mjs` renders the page and each editor through Vite SSR, `modelCapabilities.test.mjs` covers the capability presets.
