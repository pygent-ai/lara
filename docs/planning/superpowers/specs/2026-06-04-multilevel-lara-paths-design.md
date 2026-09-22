# Multilevel Lara Paths Design

## Goal

Add a two-level Lara directory model:

1. User-level Lara directory: `~/.lara`
2. Project-level Lara directory: `<workspace_root>/.lara`

The important behavior is split by prompt channel:

- The static system prompt explains the path model and execution boundary: where the user Lara directory is, where the project Lara directory is, and that bash and file tools remain relative to the project workspace.
- The existing user-prompt resource injection carries resource context, such as available skills. This resource context should keep its current placement and add user/project path source fields to the existing skills entries.

This keeps long or changing resource lists out of the session-cacheable static prompt while preserving the current available-skills user prompt behavior.

## Existing Architecture Fit

The current code already has most of the right pieces:

- `RunConfig` stores `workspace_root` and `lara_root`.
- `load_run_config()` resolves `workspace_root` and project `lara_root`.
- `AgentContextManager` accepts a single `skills_dir` and uses it for skill discovery.
- `PromptRenderContext` carries `workspace_root`, `session_dir`, and `skills_dir`.
- Static system modules are rendered through `StaticPromptSessionCache`.
- `_render_system_identity_prompt()` and related static modules are the correct place to describe stable path rules.
- `AgentRuntimeAdapter.run_turn()` wraps each user message with `wrap_user_message()` and appends current user-prompt reminders when needed.
- The current skill prompt path already renders `<skills-context>` and records consumption in `state/skill_context.json`.
- New skills created by tools are detected through `_detect_new_skills_after_file_change()` and appended to the tool message through the shared tool-side `<system-reminder>` renderer.

The new design should extend those paths instead of creating a second prompt system.

## Path Model

Introduce resolved paths:

```python
@dataclass(slots=True)
class LaraPathConfig:
    workspace_root: str
    project_lara_root: str
    user_lara_root: str
```

Default resolution:

```text
workspace_root     = existing RunConfig.workspace_root
project_lara_root  = existing RunConfig.lara_root, default <workspace_root>/.lara
user_lara_root     = Path.home() / ".lara"
```

Add fields directly to `RunConfig` at first:

```python
user_lara_root: str
project_lara_root: str | None = None
```

Compatibility rule:

- Keep `RunConfig.lara_root` as the project Lara root for existing code and persisted session layout.
- `project_lara_root` can be an alias used in prompts and future code.
- Do not move sessions to the user directory.

## Directory Responsibilities

User-level directory:

```text
~/.lara/
  config.yaml
  skills/
  prompts/
  profiles/
```

Project-level directory:

```text
<workspace_root>/.lara/
  skills/
  prompts/
  sessions/
  state/
  logs/
```

Rules:

- `sessions`, trace logs, diff artifacts, and runtime state stay project-local.
- User-level resources are reusable across projects.
- Project-level resources are only visible for the current project.
- Project-level resources override user-level resources with the same resource id.

## Tool Path Boundary

Tool path behavior must not change:

- `bash` runs with cwd set to `workspace_root`.
- `read`, `write`, `edit`, `glob`, and `grep` resolve relative paths against `workspace_root`.
- Write/edit/delete operations remain constrained by workspace rules.
- User-level `.lara` is a resource source, not the tool working directory.

The static system prompt should say this explicitly so the model does not infer that user-level resources change file operation semantics.

Suggested static prompt addition:

```text
# Lara Paths

- Workspace root: {workspace_root}
- Project Lara root: {project_lara_root}
- User Lara root: {user_lara_root}
- Bash commands and file tools resolve relative paths from the workspace root.
- Project Lara resources belong to this workspace. User Lara resources are reusable across projects.
- When the same resource exists at both levels, the project-level resource is selected and the user-level resource is shadowed.
```

This belongs in a static prompt module, for example `system.path_policy`, because the path roots are stable for a session.

## Resource Model

Add small resource dataclasses in `agent.py` or a new `resources.py` module.

```python
@dataclass(slots=True, frozen=True)
class ResourceRoot:
    scope: Literal["user", "project"]
    root: Path


@dataclass(slots=True, frozen=True)
class ResourceRef:
    id: str
    kind: Literal["skill"]
    scope: Literal["user", "project"]
    path: Path
    uri: str
    name: str
    description: str
    content_hash: str
    shadowed: tuple[str, ...] = ()
```

For skills, the resource id should be the skill frontmatter `name` when present. The directory name can be used only as a fallback if a future feature supports resources without frontmatter. Current standard skills require frontmatter, so implementation can keep using `_read_skill_definition()`.

Resource URIs:

```text
user://skills/{name}/SKILL.md
project://skills/{name}/SKILL.md
```

These URIs are prompt/audit identifiers. They do not replace filesystem paths.

## Skill Discovery And Merge

Replace single-directory skill discovery with a two-root resolver:

```python
def _scan_multilevel_skills(
    *,
    user_skills_dir: Path,
    project_skills_dir: Path,
) -> list[dict[str, Any]]:
    ...
```

Merge order:

1. Scan `~/.lara/skills`
2. Scan `<workspace_root>/.lara/skills`
3. If names collide, keep the project skill and record the user skill as shadowed

Example:

```text
~/.lara/skills/code-review/SKILL.md
<workspace_root>/.lara/skills/code-review/SKILL.md
```

Selected skill:

```text
project://skills/code-review/SKILL.md
```

Shadowed skill:

```text
user://skills/code-review/SKILL.md
```

The returned skill dict should include:

```json
{
  "name": "code-review",
  "description": "Review code changes and prioritize bugs.",
  "path": ".../.lara/skills/code-review/SKILL.md",
  "scope": "project",
  "uri": "project://skills/code-review/SKILL.md",
  "content_hash": "...",
  "shadowed": [
    {
      "scope": "user",
      "uri": "user://skills/code-review/SKILL.md",
      "path": ".../.lara/skills/code-review/SKILL.md",
      "content_hash": "..."
    }
  ]
}
```

## Prompt Placement

### System Prompt

Only stable path semantics go into static system prompt:

- workspace root
- project Lara root
- user Lara root
- tool relative path rules
- project-over-user resource precedence

Do not list all skills in the static prompt. The list changes when files are added or removed and must remain in the current user-prompt available-skills location.

### User Prompt Injection

Skills must stay in the current user-prompt available-skills path. The change is to enrich the existing skills entries with path/source fields, not to move `available-skills` into the system prompt and not to add a second skill listing elsewhere.

Current behavior:

```xml
<user-context>
  <user-identity>default</user-identity>
  <user-message>...</user-message>
</user-context>

<system-reminder>
  <skills-context>
    <skills-directory>...</skills-directory>
    <available-skills>...</available-skills>
  </skills-context>
</system-reminder>
```

New behavior:

```xml
<system-reminder>
  <skills-context>
    <user-skills-directory>~/.lara/skills</user-skills-directory>
    <project-skills-directory>{workspace_root}/.lara/skills</project-skills-directory>
    <selection-rule>Project skills override user skills with the same name.</selection-rule>

    <available-skills>
      <skill>
        <name>code-review</name>
        <description>Review code changes and prioritize bugs.</description>
        <scope>project</scope>
        <uri>project://skills/code-review/SKILL.md</uri>
        <path>{workspace_root}/.lara/skills/code-review/SKILL.md</path>
        <shadowed>
          <skill-ref>
            <scope>user</scope>
            <uri>user://skills/code-review/SKILL.md</uri>
            <path>~/.lara/skills/code-review/SKILL.md</path>
          </skill-ref>
        </shadowed>
      </skill>
    </available-skills>
  </skills-context>
</system-reminder>
```

This is intentionally still the same available-skills block in the user prompt path. It is not part of the static prompt cache or request-system prompt.

The implementation must preserve the effective injection point for available skills by appending initial skill context to the user message in a shared `<system-reminder>` wrapper. Do not reintroduce `runtime.skill_reminder` as a prompt module.

One-shot reminders after tools create, edit, or shadow skill resources should use the same merged resource renderer and keep their follow-up placement on the tool message.

## Required Code Changes

### `schema/models.py`

Extend `RunConfig`:

```python
user_lara_root: str | None = None
```

In `__post_init__`:

- Resolve `user_lara_root` to `Path.home() / ".lara"` when not supplied.
- Keep `lara_root` as the project Lara root.

### `config.py`

Update `load_run_config()`:

- Resolve `user_lara_root = Path.home() / ".lara"`.
- Optionally allow `LARA_USER_ROOT` later, but the first design uses the default only because the user selected the default.
- Pass `user_lara_root` into `RunConfig`.

Do not change `configured_lara_root`; it continues to resolve the project Lara root.

### `agent.py`

Update `PromptRenderContext`:

```python
user_lara_root: Path
project_lara_root: Path
user_skills_dir: Path
project_skills_dir: Path
```

Keep `skills_dir` temporarily as an alias for `project_skills_dir` if that reduces churn, but new code should use the explicit fields.

Update `AgentContextManager.__init__()`:

- Accept `user_lara_root`.
- Derive `project_lara_root` from `config.lara_root` or `workspace_root / ".lara"`.
- Derive:

```python
self.user_skills_dir = self.user_lara_root / "skills"
self.project_skills_dir = self.project_lara_root / "skills"
```

Update `LaraAgent.start_run()`:

- Pass `user_lara_root=Path(self.config.user_lara_root)`.
- Pass `project_lara_root=Path(self.config.lara_root)`.
- Stop treating a single `skills_dir` as the complete resource universe.

Add static module:

```python
PromptModule(
    id="system.path_policy",
    phase="static",
    type="policy",
    cache_scope="session",
    order=35,
    render=_render_system_path_policy_prompt,
)
```

Order `35` places it after injection guard and before coding rules.

Update skill functions:

- `_default_skill_context_state()` should store `user_skills_dir`, `project_skills_dir`, and a combined fingerprint.
- `_load_skill_context_state()` should tolerate old `skills_dir` state for compatibility.
- `_skills_dir_fingerprint()` should become `_skills_fingerprint(user_skills_dir, project_skills_dir)`.
- `_scan_standard_skills()` can remain as a single-root helper.
- Add `_scan_multilevel_skills()` to merge the two roots.
- `_render_skill_entries()` should render `scope`, `uri`, `path`, and optional `shadowed`.

Update detection:

```python
def _detect_new_skills_after_file_change(
    session_dir: Path,
    *,
    user_skills_dir: Path,
    project_skills_dir: Path,
) -> list[dict[str, Any]]:
    ...
```

On bash/write/edit, call it once with both roots. This catches:

- a new project skill created under `<workspace_root>/.lara/skills`
- a new user skill created under `~/.lara/skills`
- a project skill that shadows an existing user skill

For tool-message reminders, replace `_render_new_skill_tool_message_reminder(skills_dir, entries)` with a function that renders both directories and the selected skill entries.

### `runtime.py`

Keep `wrap_user_message()` and the existing user-prompt injection flow unchanged except for the rendered skill data.

Do not add a new initial skill injection helper if it would move or duplicate `available-skills`. The implementation should update the current path that already adds available skills to the user prompt so each skill entry includes:

- selected scope
- selected URI
- selected path
- shadowed user/project entries when relevant

Move shared skill scanning/rendering helpers into `src/lara/resources.py` only if both `runtime.py` and `agent.py` need to call the same renderer. If the current code has one effective skill renderer, keep the helper local and avoid unnecessary indirection.

## Session State

Current state file:

```text
<project_lara_root>/sessions/{session_id}/state/skill_context.json
```

Suggested new shape:

```json
{
  "initial_skill_context_injected": true,
  "user_skills_dir": "C:\\Users\\Administrator\\.lara\\skills",
  "project_skills_dir": "E:\\Projects\\lara\\.lara\\skills",
  "skills_fingerprint": "...",
  "known_skills": {
    "code-review": {
      "name": "code-review",
      "scope": "project",
      "uri": "project://skills/code-review/SKILL.md",
      "path": "E:\\Projects\\lara\\.lara\\skills\\code-review\\SKILL.md",
      "description": "Review code changes and prioritize bugs.",
      "content_hash": "...",
      "shadowed": [
        {
          "scope": "user",
          "uri": "user://skills/code-review/SKILL.md",
          "path": "C:\\Users\\Administrator\\.lara\\skills\\code-review\\SKILL.md",
          "content_hash": "..."
        }
      ]
    }
  },
  "pending_new_skills": [],
  "pending_system_reminders": []
}
```

Compatibility:

- If old state has `skills_dir` and `skills_dir_fingerprint`, treat it as project-only state.
- On next successful skill context render, rewrite state into the new shape.

## Configuration Precedence

For this design, only path defaults are required:

```text
workspace_root      existing CLI/env/config behavior
project_lara_root   existing lara_root behavior
user_lara_root      Path.home() / ".lara"
```

Future config merge can be:

```text
built-in defaults
-> ~/.lara/config.yaml
-> <workspace_root>/lara.yaml
-> env
-> CLI args
```

That broader config merge is not required for the first implementation.

## Tests

Add focused tests near the existing skill tests in `tests/unit/test_runtime_adapter.py`.

Unit tests:

1. Static prompt contains workspace root, project Lara root, user Lara root, and project-relative tool path guidance.
2. Static prompt does not contain `<available-skills>` or concrete skill descriptions.
3. The current user-prompt available-skills block includes `<skills-context>` when either user or project skills exist.
4. User-only skill appears with `<scope>user</scope>` and `user://skills/...`.
5. Project-only skill appears with `<scope>project</scope>` and `project://skills/...`.
6. Same-name user and project skills select the project skill and include the user skill under `<shadowed>`.
7. `skill_context.json` records the combined fingerprint and both skill directories.
8. Second request does not reinject the initial available-skills block when no relevant file changes occurred.
9. Creating a new project skill through bash/write/edit triggers a one-shot `<new-skills>` reminder.
10. Creating a project skill with the same name as an existing user skill triggers a reminder showing the project skill as selected and user skill as shadowed.

Config tests:

1. `load_run_config()` defaults `user_lara_root` to `Path.home() / ".lara"`.
2. `lara_root` remains project-local and still controls session storage.

Regression tests:

1. Existing CLI reminder behavior still works.
2. Existing single project `.lara/skills` behavior still passes when no user-level directory exists.
3. Static prompt cache remains stable across turns in the same session.

## Implementation Order

1. Add `user_lara_root` to `RunConfig` and `load_run_config()`.
2. Add path fields to `PromptRenderContext` and `AgentContextManager`.
3. Add `system.path_policy` static prompt module.
4. Extract skill scanning/rendering helpers into `resources.py` only if needed to avoid duplicate render logic.
5. Implement two-root skill scanning and project-over-user merge.
6. Update the existing user-prompt available-skills renderer to render merged skills with source paths.
7. Update the existing tool-message new-skill reminder to use the same merged skill entry shape.
8. Update skill session state shape with backward compatibility.
9. Add tests for static path prompt, merged skills, shadowing, and one-shot reminders.

## Non-Goals

- Do not move session data to `~/.lara`.
- Do not make bash or file tools relative to `~/.lara`.
- Do not put complete skill listings into the static system prompt.
- Do not implement full user/project config merging in the first pass.
- Do not load full `SKILL.md` bodies into prompt automatically. The prompt should list names, descriptions, paths, and scopes; full instructions should be loaded only when a skill is actually used.

## Follow-Up Decisions

- Whether to expose `LARA_USER_ROOT` later. The current requested default is `~/.lara`, so the first implementation does not need an override.
- Whether user-level skills should be mutable through normal tools. The path model allows reading resources from user-level paths, but write/edit operations should continue to follow workspace safety rules unless a future explicit user-level resource editing command is added.
