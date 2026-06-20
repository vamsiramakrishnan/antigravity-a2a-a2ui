# ADR 0001 — Repurpose the control plane onto the Managed Agents (Agents + Interactions) API

Status: Accepted · Date: 2026-06-20

## Context

We built a shared, stateless control plane that serves *per-user* Antigravity
skill workspaces over A2A/A2UI, materializing a **local** runtime per session.
Gemini Enterprise has since shipped **Managed Agents** — the same Antigravity
harness, hosted in a managed sandbox — exposed through two APIs:

* **Agents API** (control plane): `aiplatform v1beta1`
  `projects/{p}/locations/{l}/agents/*` — create/get/patch/delete/list. Stores a
  **reusable agent config**: `base_agent`, `tools`, and a `base_environment`
  with `sources[]` (`GCS | INLINE | REPOSITORY | SKILL_REGISTRY` → mount
  `target`) and a `network.allowlist`.
* **Interactions API** (data plane): `client.interactions.create({agent, input,
  environment, stream, store})` — retrieves the stored config and runs the
  harness.

### What we verified (authoritative sources)

From Google's public discovery docs and the agent-platform reference samples:

1. **`StreamAssist.ToolsSpec` has no skill field.** Exactly four sub-specs:
   `vertexAiSearch`, `webGrounding`, `imageGeneration`, `videoGeneration`.
   Skills attach via `@mention` query text and surface in the response as
   `StreamAssistResponse.InvokedSkill {name, displayName}`. `userMetadata` is
   only `{timeZone, preferredLanguageCode}` — **not** an identity.
2. **The Interactions turn carries no identity/credential/scope.** All scope
   lives on the stored agent config. `EnvironmentConfigSource` is
   `{type, source, target, content, encoding}` — **no credential, no
   principal field.** The config is explicitly a *"Reusable Agent Config"*.
3. **Therefore isolation is the "frozen" model:** scope is pinned at
   `agents.create`/`patch`, keyed to the agent, **not** per end-user per turn.
4. **The platform is silent on multi-user / per-user isolation / runtime
   identity.** It models *one reusable agent*, not tenancy.
5. **The sandbox mount layout matches our format**: skills mount as
   `/.agent/skills/<skill>/{SKILL.md, scripts/}` — the agentskills.io layout we
   already emit. Pre-installed: `gcloud CLI`, `google-genai`, Python 3.11,
   Node 20. Skill Registry skills carry a native `sha256` and `SkillRevision`s.

### The consequence

A single stored config is **reused across all callers** that hit that agent.
Per-user isolation therefore **requires one agent per user**, each with its
`base_environment.sources` scoped to that user's GCS prefix / skills. That
provisioning + identity→agent mapping + credential/mount brokering **is our
control plane** — nothing the platform provides.

## Decision

**Wrap the Managed Agents API.** Stop materializing a local runtime; repoint the
control plane to provision **per-user managed agents** (the "Agent Setup" plane)
and broker real end-users into **scoped interactions** (the "Usage" plane).

### Keep / Repoint / Retire

| Module | Verdict | New job |
| --- | --- | --- |
| `identity/` (Principal, verify, ToolCredential) | **Keep** | `(iss,sub)` → derive **per-user agent id**; gate the gateway; cred for registry-publish + GCS provisioning |
| `identity/session_token` | **Repoint** | auth for the HTTP **MCP server** the sandbox calls |
| `provisioning/` | **Promote** | idempotent `agents.create`/`patch` with per-user scoped sources + IAM. **Isolation is enforced here.** |
| `registry/drafts.py` | **Keep** | governance gate (validate/policy) before `skills.create/patch` |
| `registry/` revisions/generations | **Thin** | registry owns revisions (native `sha256`/`SkillRevision`); keep "active revision per user" pointer |
| `storage/` (gcs, layout, guards) | **Repoint** | manage + guard the per-user GCS prefix the agent mounts; upload staging |
| `broker/` | **Keep** | mint user-scoped cred; reconcile vs. sandbox runtime identity |
| `gemini_enterprise/skill_registry.py` | **Keep (central)** | publish/activate path |
| `gemini_enterprise/skill*.py`, `skill_io.py` | **Keep** | format is native (zero transform) |
| `gemini_enterprise/tools.py` + `gateway/enterprise_api.py` | **Repoint** | become the HTTP MCP server the sandbox calls |
| `gemini_enterprise/client.py` | **Repoint** | add Agents + Interactions API; keep streamAssist for connector-grounded Q&A |
| `session/lifecycle.py` | **Repoint** | verify → ensure agent → `interactions.create` |
| `gateway/a2a.py` | **Keep** | A2A card discovery; invoke drives `interactions.create` |
| `messaging.py` | **Repoint** | stage uploads to GCS prefix → `gcs`/`INLINE` source |
| `antigravity/config_builder.py` | **Repurpose** | emit `agents.create/patch` body + a hardened `LocalAgentConfig` |
| `materializer/` | **Retire** (keep digest check optional) | mount + native `sha256` replace it |
| `session/connection.py` (local no-cred runtime) | **Retire** | replaced by remote sandbox |

### The one open question (must be measured, not inferred)

The platform does not state what identity the harness uses to read mounted
sources. This decides whether "one agent per user + per-prefix IAM" is
**enforced** isolation or whether `storage/` guards remain the real boundary.
Measure with `scripts/probe_sandbox_identity.mjs` (a `gcloud auth
print-access-token` / `tokeninfo` interaction turn). Until then we keep the
storage-adapter guard **and** add in-runtime policy as defense-in-depth (ADR 2).

## Consequences

* ~70% of modules keep their responsibility and change target API; only the
  *local-runtime substitute* is deleted.
* The moat — per-user identity→agent mapping, prefix/skill scoping, credential
  broker, draft→publish gate — survives intact because the platform lacks it.
* New dependency on a **pre-GA** API; isolation guarantees partially shift into
  Google's sandbox. Mitigated by per-user agents + IAM + in-runtime policy.
