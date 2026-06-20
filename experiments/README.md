# experiments — settle the open isolation questions

Automated probes that answer, against **your** Gemini Enterprise project, the
questions left open in [ADR 0001](../docs/adr/0001-repurpose-onto-interactions-api.md):
what identity the Antigravity sandbox runs as, whether per-user isolation is
actually enforced, which hardening controls the managed Agents API accepts, and
whether the sandbox can reach our `/mcp` gateway.

Each probe provisions a **throwaway** managed agent, runs one diagnostic
interaction, classifies the result, and (by default) deletes the agent. The run
writes a single `results/report-*.md` — **paste that back** and it unblocks the
remaining work (MCP router mount + trimming/hardening the isolation layer).

## Setup

```bash
cd experiments
cp .env.example .env          # fill PROJECT_ID and GCS_BUCKET (at minimum)
npm install                   # pulls @google/genai
gcloud auth application-default login   # or run where ADC is available

# seed two tenant prefixes so cross-tenant reads have something to find
echo hi | gcloud storage cp - $GCS_BUCKET/workspaces/userA/hello.txt
echo hi | gcloud storage cp - $GCS_BUCKET/workspaces/userB/hello.txt
```

Requires: the `aiplatform` / Gemini Enterprise Agent Platform API enabled, and a
`base_agent` you can use (default `antigravity-preview-05-2026`).

## Run

```bash
npm run all            # all five experiments → results/report-<stamp>.md
# or individually:
npm run identity       # 01  sandbox runtime identity
npm run two-agents     # 02  per-agent vs shared SA
npm run cross-tenant   # 03  cross-tenant reach (the direct proof)
npm run controls       # 04  does agents.create accept capabilities/policies? is allowlist enforced?
npm run mcp            # 05  /mcp reachability (needs GATEWAY_URL)
npm run cleanup        # delete any leftover probe agents
```

## What each result means (and what I change because of it)

| Experiment | Status | What it tells us → action |
| --- | --- | --- |
| 01 identity | `no-ambient-creds` | sandbox has no token → keep storage guard as defense-in-depth only |
| | `ambient-creds` | a token exists → 02/03 decide if it isolates |
| 02 two-agents | `per-agent-sa` | distinct SA per agent → per-prefix IAM enforces isolation |
| | `shared-sa` | one SA → IAM can't isolate alone; provisioner + guard are load-bearing |
| 03 cross-tenant | `isolated` | denied other prefix → mount/IAM scoping holds |
| | `cross-readable` | read other tenant → guard stays the real boundary; harden provisioning |
| 04 controls | `fields-accepted` | managed agents honor capabilities/policies → push hardening into the API |
| | `fields-rejected` | hardening only applies to a self-hosted harness → rely on allowlist + tool selection |
| 05 mcp | `tool-visible` / logs show `/mcp` hit | reachable → I wire + mount the MCP router with the right token model |
| | `skipped` | set `GATEWAY_URL` once the gateway is deployed |

## Notes

- The probes only touch agents named `probe-*` and your own bucket prefixes.
- `KEEP=1 npm run all` keeps the agents for manual inspection.
- `results/` is git-ignored; the report contains only your project/bucket names
  and the sandbox's own diagnostic output (the MCP token is redacted).
