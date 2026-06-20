# antigravity-a2a-a2ui

A2A integration and A2UI for Antigravity: a **shared, stateless control plane**
that serves a **private, per-user Antigravity skill workspace** to Gemini
Enterprise users — without ever giving the shared service broad access to any
user's data.

The core idea: tenant isolation is enforced by **Cloud Storage under a per-user
credential**, derived from a **verified OAuth `(issuer, subject)`** identity —
not by a shared runtime identity, an email from a prompt, or a GCS FUSE mount.

See [`docs/architecture.md`](docs/architecture.md) for the full design and
rationale.

## At a glance

```
Gemini Enterprise ──> A2A/A2UI Gateway (Cloud Run, no broad GCS access)
                         ├─ Workspace Registry  (identity → workspace, revisions, generations)
                         ├─ Credential Broker   (delegated user OAuth, or CAB-downscoped)
                         ├─ Trusted StorageAdapter (per-request, workspace-scoped)
                         ├─ Session Materializer (download immutable revision + verify digest)
                         └─ Antigravity session  (pinned generation, read-only skills)
```

Two identity planes are kept apart in code:

* **agentAuthorization** → verified `Principal{issuer, subject}` → workspace id.
* **toolAuthorization** → opaque `ToolCredential`, handed only to the storage
  adapter, **never** to the model or the Antigravity connection.

## Quickstart

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                      # 31 tests covering the isolation invariants

# Run the gateway locally (dev identity + local-filesystem "bucket")
export A2A_ALLOW_INSECURE_DEV=true
export A2A_STORAGE_LOCAL_ROOT=/tmp/a2a-bucket
python -m a2a_workspace      # serves on :8080
```

Dev auth uses an insecure token `Authorization: Bearer <issuer>|<subject>|<email>`
(production uses a signed OIDC JWT — set `A2A_IDENTITY_BACKEND=jwt`).

End-to-end: author a skill through the bounded tools, then invoke.

```bash
AUTH="Authorization: Bearer https://idp|alice|alice@example.com"

DID=$(curl -s -XPOST localhost:8080/workspaces/me/drafts -H "$AUTH" \
      -H 'Content-Type: application/json' -d '{}' | jq -r .draft_id)

manifest=$(printf '{"name":"greeter"}' | base64 -w0)
skill=$(printf "print('hi')" | base64 -w0)
curl -s -XPUT localhost:8080/workspaces/me/drafts/$DID/files -H "$AUTH" \
     -H 'Content-Type: application/json' \
     -d "{\"path\":\"manifest.json\",\"content_base64\":\"$manifest\"}"
curl -s -XPUT localhost:8080/workspaces/me/drafts/$DID/files -H "$AUTH" \
     -H 'Content-Type: application/json' \
     -d "{\"path\":\"skill.py\",\"content_base64\":\"$skill\"}"

curl -s -XPOST localhost:8080/workspaces/me/drafts/$DID/validate -H "$AUTH"
curl -s -XPOST localhost:8080/workspaces/me/drafts/$DID/submit   -H "$AUTH"
curl -s -XPOST localhost:8080/workspaces/me/drafts/$DID/publish  -H "$AUTH" \
     -H 'Content-Type: application/json' -d '{"activate":true}'

curl -s -XPOST localhost:8080/a2a/invoke -H "$AUTH"   # → pinned conversation + credential-free connection
```

## HTTP surface

| Method & path | Purpose |
| --- | --- |
| `GET /.well-known/agent.json` | A2A agent card advertising both auth planes |
| `POST /a2a/invoke` | Provision + materialize active generation; start a pinned conversation |
| `GET /workspaces/me` | The caller's own workspace metadata |
| `POST /workspaces/me/drafts` | Open a draft (optionally from a base revision) |
| `PUT /workspaces/me/drafts/{id}/files` | `apply_patch` (base64 body; `null` deletes) |
| `POST /workspaces/me/drafts/{id}/validate` \| `/submit` \| `/publish` | Bounded publish pipeline |

Every workspace route resolves the workspace from the **verified principal**
(`/me`) — never from a path parameter — so a caller can only act on their own
workspace.

## Configuration

All via environment (see `src/a2a_workspace/config.py`). Key switches:

| Variable | Default | Notes |
| --- | --- | --- |
| `A2A_IDENTITY_BACKEND` | `dev` | `jwt` for production (`A2A_OIDC_ISSUER/AUDIENCE/JWKS_URI`) |
| `A2A_ALLOW_INSECURE_DEV` | `false` | must be `true` to enable the dev verifier |
| `A2A_STORAGE_BACKEND` | `local` | `gcs` requires the `gcp` extra |
| `A2A_REGISTRY_BACKEND` | `memory` | `firestore` requires the `gcp` extra |
| `A2A_STORAGE_BUCKET` | `skills-local` | one bucket per org × env × region |

Production adapters install with `pip install -e '.[gcp]'`.

## Gemini Enterprise connectors & agent-to-agent

The agent can answer from the user's connectors (SharePoint, Jira, GitHub,
Salesforce, …) and delegate to other registered Gemini Enterprise agents — via
the Discovery Engine API — **without ever holding an OAuth credential**. The
Antigravity tools are thin proxies to the control plane, which holds the user's
delegated token and makes the credentialed `:streamAssist` call.

Setup is one command plus a few env vars:

```bash
# Point at your Gemini Enterprise app
export A2A_GE_PROJECT=my-project A2A_GE_ENGINE=my-app-id   # location defaults to "global"
export A2A_PUBLIC_URL=https://my-gateway.run.app
export A2A_SESSION_TOKEN_SECRET=$(openssl rand -hex 32)

# Generate the connectors skill bundle (SKILL.md + proxy tools), then publish it
python -m a2a_workspace gen-enterprise-skill ./enterprise-skill
```

At invoke time, if the request carries the user's delegated token, the gateway
mints a short-lived **session proxy token**, stashes the user token server-side
for the conversation, and drops a `app_data_dir/.a2a/session.json` the proxy
tools read. The agent then has four tools: `search_enterprise`,
`answer_with_web`, `list_enterprise_agents`, `invoke_enterprise_agent`.

| Proxy endpoint (called by the agent runtime) | Purpose |
| --- | --- |
| `POST /enterprise/assist` | Grounded answer over the user's connectors |
| `POST /enterprise/agents/list` | List registered agents that can be invoked |
| `POST /enterprise/agents/invoke` | Delegate a query to another agent |

These require the session proxy token (not the user token) and fail closed if no
user credential is associated with the session.

## Layout

```
src/a2a_workspace/
  identity/      two identity planes: Principal, verifiers, opaque ToolCredential
  registry/      workspace/revision/generation models + bounded draft→publish tools
  storage/       trusted StorageAdapter (local + GCS), workspace key layout & guards
  broker/        credential broker: delegated OAuth or CAB-downscoped credentials
  materializer/  content-addressing + download-and-verify into an isolated tree
  session/       lifecycle, generation-pinned conversations, credential-free connection
  provisioning/  idempotent first-touch workspace + managed-folder IAM
  gemini_enterprise/  Discovery Engine client + credential-free proxy tools + skill bundle
  antigravity/   Antigravity SDK wiring (LocalAgentConfig builder, session file)
  gateway/       FastAPI app: A2A + Workspace REST + enterprise proxy endpoints
  container.py   composition root (the only place concrete backends are named)
tests/           isolation, integrity, lifecycle, broker, and gateway tests
```

## License

Apache-2.0. See [LICENSE](LICENSE).
