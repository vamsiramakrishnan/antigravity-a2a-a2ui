# Sandbox isolation experiment report

- project: `vital-octagon-19612` · location: `global` · base_agent: `antigravity-preview-05-2026`
- bucket: `gs://vital-octagon-19612-a2a-iso-probe` · prefixes: `workspaces/userA` / `workspaces/userB`
- gateway: `(not set)` · generated: 2026-06-21T01:26:40Z
- runner: live-tenant preflight + customized Managed-Agents Python notebook (`experiments/notebooks/isolation_experiments.ipynb`)

## Operator summary

- **01 identity** — `pending-run`: agent provisions, but the Interactions API returned **0 bytes** from this automation environment, so the whoami/token/metadata probe could not be read here. Run the notebook to capture the sandbox SA email + token scopes.
- **02 two-agents** — `pending-run`: same blocker; needs interaction output to compare the two sandboxes' identities (shared-SA vs per-agent-SA).
- **03 cross-tenant** — `pending-run`: same blocker; needs interaction output to see whether the A→B cross-prefix read is `AccessDenied` or succeeds.
- **04 controls** — `partially-established`: `agents.create` over **raw REST accepted** an agent body that included `capabilities`/`policies` (HTTP 200) — i.e. the API did not reject the fields. Whether they are *enforced* still needs an interaction (pending-run).
- **05 mcp** — `pending-run` / `skipped`: no gateway deployed; the notebook falls back to the public grep MCP to prove reachability once interactions stream.
- Project `vital-octagon-19612`; base_agent `antigravity-preview-05-2026` (valid, no substitution needed). **SDK fixes made** in `experiments/lib/platform.mjs`: `deleteAgent` now passes the short id string first (matches `@google/genai` v2.9.0 `delete(id: string)`); `runInteraction` retries through the provisioning window and now sends `background:true`. **API errors hit:** `400 Setting background=true is required`; `400 Resource setup is in progress`; `404 Requested entity was not found` / `"Agent … is being created"` during the ~1–2 min async-create window; and a hard **0-bytes / connection-held** behavior on every streaming or non-streaming `background:true` interaction from this environment (base agent included), which is what blocks 01/02/03/05 here.

## What was verified live (facts established against the tenant)

These were confirmed by direct calls (SDK + raw REST) against `vital-octagon-19612`:

1. **Preflight green.** `aiplatform.googleapis.com` enabled; ADC available; bucket `gs://vital-octagon-19612-a2a-iso-probe` created (`us-east1`) and seeded with `workspaces/userA/hello.txt` and `workspaces/userB/hello.txt`.
2. **`agents.create` works** via the JS SDK (`@google/genai` 2.9.0), the Python SDK (`google-genai` 2.4.0), and raw REST
   (`POST .../v1beta1/projects/<id>/locations/global/agents`). It returns a **long-running operation**
   (`.../operations/<n>`); `operation.done` flips true within ~2 s.
3. **Creation is async at the data plane.** For ~1–2 minutes after create, interactions/`agents.get` return, in sequence:
   `404 Requested entity was not found` → `"Agent … is being created and cannot be deleted"` → `400 Resource setup is in progress`.
   The harness now retries through this window.
4. **Interactions API contract** (from the official docs + observed errors):
   - `background:true` is **required** for these code-execution workflows (`background:false` → `400 invalid_request`).
   - Raw REST also requires the header **`Api-Revision: 2026-05-20`** and an `input` **array** of
     `{"type":"user_input","content":[{"type":"text","text":"…"}]}` objects (the SDK accepts a plain string and converts it).
   - Endpoint is top-level `.../locations/global/interactions` with `agent` in the body (base-agent name or custom agent id).
5. **`capabilities`/`policies` are accepted by the create API** (HTTP 200 over REST). The JS SDK forwards unknown body
   fields untouched (`encodeJSON` pass-through); the Python SDK would `TypeError` on them, so the notebook's exp-04 uses raw REST.
6. **base_environment round-trip caveat.** An agent created with `base_environment.sources` (GCS mount) + `network`
   comes back from `agents.get` as just `{"type":"remote"}` — the `sources`/`network` are **not echoed** on read.
   This needs confirmation (GET projection vs. not-persisted) because experiments 03/04 depend on the mount actually applying.
7. **Hard blocker for 01/02/03/05 in this environment:** every `background:true` interaction (streaming *and*
   non-streaming, custom agent *and* base `antigravity-preview-05-2026`, JS SDK, Python SDK, and raw `curl`) held the
   connection open and delivered **0 bytes** for 90–280 s before timing out. `background:false` returns an immediate JSON
   error on the same channel, so connectivity/auth is fine — the runtime simply produced no interaction output here.

## How to finish the five experiments

Run **`experiments/notebooks/isolation_experiments.ipynb`** top-to-bottom from an environment where interactions stream
(Colab in the tenant, or a workstation with ADC). It is pre-filled for this project and reproduces all five probes on the
official streaming pattern, then writes the completed `experiments/findings/LATEST.md` (and a timestamped copy) and deletes
every `probe-*` agent. Paste that regenerated report back to close 01/02/03/05 and the enforcement half of 04.

## Per-experiment status

| # | Experiment | Status | Reading |
| --- | --- | --- | --- |
| 01 | Sandbox runtime identity | **pending-run** | Blocked by the 0-byte interaction behavior in this environment. |
| 02 | Per-agent vs shared SA | **pending-run** | Needs interaction output from two agents to compare identities. |
| 03 | Cross-tenant reach | **pending-run** | Needs interaction output to see AccessDenied vs cross-read. |
| 04 | Hardening controls | **fields-accepted (create) / enforcement pending** | REST create accepted `capabilities`/`policies` (HTTP 200); enforcement needs an interaction. |
| 05 | MCP reachability | **skipped / pending-run** | No gateway deployed; notebook falls back to public grep MCP once interactions stream. |

## Probe cleanup

All `probe-*` agents created during this run (`probe-rest-1`, `probe-py-*`, `probe-shape-*`, `probe-identity-a`) were
deleted; `agents.list` returns no agents. The notebook's final cell repeats this cleanup.

---
Generated by the live-tenant preflight. Run the notebook to produce the completed isolation report.
