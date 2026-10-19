# Settings feature

`SettingsPage.jsx` owns the full-workspace page, category navigation, dirty-state close guard, and save footer. The workbench stays mounted but hidden while this page is active.

- `useSettingsDraft.js`: shared draft state, catalog loading, model discovery, and reference-aware edit operations.
- `settingsModel.js`: native draft conversion, factories, validation, and the pure quick-add operation.
- `AddModelForm.jsx`: the single add-model form inside the model directory; selecting a connection automatically discovers its models, keeps manual ID entry as a fallback, and adds the result to an existing or new group in one action.
- `ConnectionSettings.jsx`: reusable service credentials and protocol endpoints.
- `ModelSettings.jsx`: manual model identity and capability editing.
- `GroupSettings.jsx`: group membership, default group, ordered fallback management, and a collapsible global retry policy (attempt count and idle timeout).
- `WorkspaceSettings.jsx` and `RuntimeSettings.jsx`: project defaults and execution/permission settings.
- `settings.css`: feature layout and responsive styles; shared form primitives remain in the app stylesheet.

Service connections, models, and model groups appear together under model configuration. Connection and model cards always expose essential editable fields and a summary; model cards summarize group membership, which is edited in the group tab. Group membership, fallback order, and the default group are visible without expansion. Only optional connection/model details and global retry settings collapse. Known missing credentials are reported in the card and prevent saving; unverified connections are never labeled as tested. Adding a model creates ordinary native model and group records; it does not merge their schemas, duplicate credentials, replace existing models, or reorder existing group members. Payload serialization remains in the existing shared API client.

Tests in `settingsModel.test.mjs` cover relationship preservation, existing priorities and credentials, collisions, catalog capabilities, and invalid references. App render tests retain coverage through compatibility exports in `App.jsx`.

ModelCapabilitiesEditor presents one capability-template selector (purpose presets and Pygent catalog models), actual capability tags, and all native Pygent capability fields in detailed controls. modelCapabilities.js preserves limits on template changes and enforces streaming/output and reasoning dependencies. Purpose names remain UI-only; saved native capability structure is unchanged.

Connections render in two modes. Built-in providers show provider and API Key, collapsing the endpoint into a catalog-default URL caption; a link reveals the editable `Base URL + protocol` rows for custom addresses, and the editor also appears whenever the saved URL differs from the catalog default or catalogs are unavailable. Custom providers show a provider name (which doubles as the connection name), API Key, and editable endpoint rows. Both modes offer a connection test that calls model discovery with the unsaved credential value, a "从此连接添加模型" shortcut that opens the add-model form preset to the connection, and Advanced Settings (authentication mode, local name for built-in providers, credential reference, proxy, TLS). The no-authentication path stays reachable from the key field. New connections receive a unique credential reference plus one OpenAI Chat Completions endpoint. Existing multi-protocol connections retain and display every endpoint.

The model card's protocol selector appears only when its connection offers more than one protocol or the saved protocol is no longer configured; single-protocol models show the protocol in the summary only.

The connection card groups catalog selection and endpoints in one place. Selecting a Pygent built-in provider applies its catalog protocols, default URLs, and credential name while carrying an unsaved API key to the new credential reference. Selecting Custom keeps the current endpoints for manual editing. Each endpoint is one `Base URL + protocol` row; changing its protocol updates models that reference that endpoint. The local name, authentication mode, credential reference, proxy, and TLS remain under Advanced Settings.
