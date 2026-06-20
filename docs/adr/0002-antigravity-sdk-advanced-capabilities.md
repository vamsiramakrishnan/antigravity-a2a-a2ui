# ADR 0002 — Exploit the Antigravity SDK's advanced surface (hooks, policies, triggers)

Status: Accepted · Date: 2026-06-20

The Antigravity SDK (`google-antigravity==0.1.4`, public on PyPI; requires
`google-genai>=1.0`, `mcp>=1.0`, `websockets`) exposes far more than the
`tools` + `skills_paths` we currently use. `AgentConfig` / `LocalAgentConfig`
accept: `system_instructions`, `capabilities`, `tools`, **`policies`**,
**`hooks`**, **`triggers`**, `mcp_servers`, **`workspaces`**, `conversation_id`,
`save_dir`, `app_data_dir`, `response_schema`, `skills_paths`, `model`/`models`.

We adopt the advanced surface as follows.

## 1. Policies — in-runtime tool authorization (defense-in-depth isolation)

`google.antigravity.policy`: `allow`, `deny`, `ask_user`, `allow_all`,
`deny_all`, `workspace_only(dirs)`, `safe_defaults`, `confirm_run_command`;
`Decision ∈ {APPROVE, DENY, ASK_USER}`; rules support `when=<Predicate>`.

Decisions:
* **`workspace_only([session_work_dir])`** confines file tools to the user's
  session tree — a second isolation boundary inside the runtime.
* **`deny("run_command", when=...)`** blocks credential-exfil patterns
  (`gcloud auth print-access-token`, `curl .../tokeninfo`, metadata-server
  hits) — directly mitigating the unresolved sandbox-identity risk (ADR 0001).
* **`ask_user`** for high-blast-radius tools (publish, delete, network writes),
  routed back over A2UI.

## 2. Hooks — audit, redaction, gating

`google.antigravity.hooks`: decorators `on_session_start`, `on_session_end`,
`on_interaction`, `on_compaction`, `on_tool_error`, `pre_turn`, `post_turn`,
`pre_tool_call_decide`, `post_tool_call`. Hook kinds: `InspectHook`
(observability), `DecideHook` (read-only blocking → `HookResult`),
`TransformHook` (modify data). `HookContext.get_state/set_state` shares state.

Decisions:
* **Audit**: `on_session_start/end`, `post_tool_call`, `on_tool_error` emit
  structured events to the control plane (our own `analyzedSessions` analogue,
  owned and exportable).
* **Redaction** (`TransformHook` / `pre_turn`): strip secrets/PII from
  model-visible data before each turn.
* **Decision gating** (`pre_tool_call_decide`): enforce policy that needs
  runtime context beyond static `policy` rules.

## 3. Triggers — proactive / scheduled skills

`google.antigravity.triggers`: `every(interval, cb)`, `on_file_change(path,
cb)`, `@trigger`; `TriggerContext.send()` pushes proactive messages.
Enables scheduled workspace skills and react-to-new-input flows (e.g. a new
upload in `inputs/` kicks a skill).

## 4. Capabilities — least-privilege tool surface

`CapabilitiesConfig(enabled_tools=[...], disabled_tools=[...],
enable_subagents=bool, compaction_threshold=int)`. `BuiltinTools ∈ {LIST_DIR,
SEARCH_DIR, FIND_FILE, VIEW_FILE, CREATE_FILE, EDIT_FILE, RUN_COMMAND,
ASK_QUESTION, START_SUBAGENT, GENERATE_IMAGE, SEARCH_WEB, FINISH}`. We disable
`RUN_COMMAND` for skill-only agents and gate `SEARCH_WEB` by network policy.

## 5. MCP over HTTP — kill the session-file hack

`McpStreamableHttpServer` lets the runtime reach our enterprise proxy as a
first-class HTTP MCP server (bearer-authed with the session proxy token),
replacing the `app_data_dir/.a2a/session.json` file-drop. Same "agent never
holds the user's GE credential" property, native transport.

## 6. Structured output & model targeting

`response_schema` (pydantic/JSON) for typed results; `model`/`models`
(`ModelTarget`) for primary + fallback model selection per workspace.

## Engineering rule

All SDK use stays **import-guarded** behind serializable specs (as
`config_builder.py` already does), so the default test suite runs without the
SDK installed. Pure decision logic (e.g. "is this command an exfil attempt?")
is factored out and unit-tested directly; the SDK wrappers only adapt it.
