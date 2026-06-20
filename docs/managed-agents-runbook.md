# Managed Agents Runbook — testing against a real Gemini Enterprise project

Operator guide for exercising the **Managed Agents** API (ADR 0001) against a
live project. It does two things:

1. **Settle ADR 0001's one open question** — measure what identity the sandbox
   harness runs as (enforced isolation vs. storage-guard-only).
2. **Prove the per-user model end-to-end** — provision a per-user agent, run an
   interaction, and mount a Skill Registry source.

Everything below is copy-pasteable. Replace the `UPPER_CASE` placeholders.
These scripts run as *you* (the operator); nothing here is executed by the
control plane.

## Prerequisites

```bash
# 1. Authenticated gcloud, with ADC for the SDK.
gcloud auth login
gcloud auth application-default login
gcloud config set project PROJECT_ID

# 2. Enable the platform API (the Managed Agents surface lives in aiplatform).
gcloud services enable aiplatform.googleapis.com --project PROJECT_ID

# 3. A per-user GCS prefix the agent will mount at /.agent.
#    One bucket per org x env x region; one prefix per user (see architecture.md).
gsutil ls gs://GCS_BUCKET/ || gsutil mb -l REGION gs://GCS_BUCKET/
gsutil cp ./SKILL.md gs://GCS_BUCKET/users/UUID/skills/demo/SKILL.md   # optional seed

# 4. Install the SDK for the probe/smoke scripts.
cd scripts && npm install      # pulls @google/genai
```

> WHY a GCS prefix and not a bucket grant: a shared runtime that can reach every
> bucket defeats isolation (architecture.md). Sources mount a *per-user prefix*;
> isolation is meant to be enforced at provisioning (ADR 0001). Step 1 measures
> whether the runtime actually honours that.

## Step 1 — Probe the sandbox identity (settles ADR 0001)

You need one existing agent with the `code_execution` tool. If you have none,
run Step 2 first, then come back.

```bash
cd scripts
PROJECT_ID=PROJECT_ID AGENT=AGENT_ID npm run probe:identity
# REST fallback (no Node):
PROJECT_ID=PROJECT_ID AGENT=AGENT_ID ../.venv/bin/python probe_runtime_identity.py
```

The probe makes the sandbox introspect its own ambient credentials and streams
the output. Scan the events for the markers (`--- whoami ---`,
`NO AMBIENT TOKEN`, a service-account email) and classify:

| Outcome | What you see | Meaning | Action |
| --- | --- | --- | --- |
| **1. No ambient token** | `NO AMBIENT TOKEN`, no `~/.config/gcloud`, metadata `none` | Runtime has no GCP identity; the platform stages mounts for it | Isolation enforced **upstream** (best). Per-prefix IAM + brokering is real. |
| **2. Scoped / downscoped token** | `tokeninfo` shows a narrow scope, short expiry, per-agent principal | Runtime holds a per-agent downscoped token | Per-agent IAM is the **enforced boundary** (good). Verify the scope is prefix-bound (CAB), not bucket-wide. |
| **3. Broad shared SA** | metadata returns a long-lived SA email; `cloud-platform` scope shared across agents | One shared, broadly-scoped service account | Per-agent IAM does **not** isolate. Keep `GuardedStorageAdapter` + ADR 2 in-runtime policy as the real boundary (worst). |

Record the outcome in ADR 0001 ("The one open question"). It decides whether the
storage-adapter guard stays load-bearing.

## Step 2 — Provision a per-user agent and run it

```bash
cd scripts
PROJECT_ID=PROJECT_ID \
AGENT_ID=user-UUID \
GCS_BUCKET=gs://GCS_BUCKET/users/UUID \
INPUT="List the files under /.agent and summarize any SKILL.md" \
  npm run provision
```

This is **idempotent**: it `agents.get` first and skips create if the agent
exists (matches `provisioning/`'s first-touch contract). It then runs an
`interactions.create` and streams events.

- Phase A creates the stored config: `base_agent=antigravity-preview-05-2026`,
  tools `code_execution|filesystem|google_search`, and a `base_environment` whose
  only source is the user's GCS prefix mounted at `/.agent`.
- Phase B runs the interaction against that agent.

Use `SKIP_INTERACTION=1` to provision only. Override `BASE_AGENT`,
`SYSTEM_INSTRUCTION`, `AGENT_DESCRIPTION`, `INPUT`, `LOCATION` as needed.

## Step 3 — Mount a Skill Registry source and re-run

Skill Registry skills carry a native `sha256` / `SkillRevision` (ADR 0001 #5)
and mount in the agentskills.io layout under `/.agent/skills`.

```bash
cd scripts
PROJECT_ID=PROJECT_ID \
AGENT_ID=user-UUID \
GCS_BUCKET=gs://GCS_BUCKET/users/UUID \
SKILL_RESOURCE_NAME=projects/PROJECT_ID/locations/global/collections/default_collection/skills/SKILL_ID \
INPUT="Run the demo skill mounted under /.agent/skills and report its output" \
  npm run provision
```

> The agent already exists from Step 2, so this **patches** intent via the same
> idempotent path. To force the new source onto an existing agent, delete it
> first (`gcloud ... agents delete`) or use a fresh `AGENT_ID`, since create is
> skipped when the agent is present.

## IAM note — A2A on Cloud Run

When the control plane exposes A2A endpoints on Cloud Run and Gemini Enterprise
(Discovery Engine) invokes them, grant the Discovery Engine service agent the
invoker role on the Cloud Run service:

```bash
gcloud run services add-iam-policy-binding A2A_SERVICE \
  --region REGION \
  --member "serviceAccount:service-PROJECT_NUMBER@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role roles/run.servicesInvoker
```

`PROJECT_NUMBER` is `gcloud projects describe PROJECT_ID --format='value(projectNumber)'`.

## Concept mapping (our control plane → Managed Agents)

| Our concept | Managed Agents primitive | Notes |
| --- | --- | --- |
| Workspace (per-user) | **Agent** (`agents.create/get/patch`) | One agent per user; sources scoped to the user's GCS prefix. Isolation is provisioned here. |
| Revision / active generation | **Skill** + **SkillRevision** (Skill Registry) | Registry owns immutable revisions with native `sha256`; we keep an "active revision per user" pointer. |
| Session / conversation | **Interaction** (`interactions.create`) | Stateless turn against the stored agent config; carries no identity (all scope lives on the agent). |
| Materialized skills tree | `base_environment.sources` → mount `target` | `gcs` prefix at `/.agent`; `skill_registry` at `/.agent/skills`. |
| Storage isolation guard | (no platform analogue) | `GuardedStorageAdapter` + ADR 2 in-runtime policy — stays load-bearing unless Step 1 shows outcome 1 or 2. |
