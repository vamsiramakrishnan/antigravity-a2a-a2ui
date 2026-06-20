# Using `agents-cli` with this repo

This repo is a [`google-agents-cli`](https://github.com/google/agents-cli)
project. The root `agents-cli-manifest.yaml` declares it, so the `agents-cli`
toolchain can build, run, deploy, and publish the control plane.

The integration has two pure, SDK-free helper layers (no `google-*` packages
required to import or test them):

- `src/a2a_workspace/integrations/agents_cli.py` — card adaptation, the publish
  argv builder, the IAM member helper, and a `manifest_dict()` mirror.
- `src/a2a_workspace/gateway/agents_cli_compat.py` — a FastAPI router that serves
  the A2A agent card at the path `agents-cli` / Discovery Engine expect.

The ADK agent lives at `app/agent.py` (the manifest's `agent_directory: app`),
exposing module-level `root_agent` and `app`. That module is import-guarded so it
loads even without `google-adk` installed; `ROOT_AGENT_SPEC` and `build_tools()`
are testable without the SDK.

## Two runtimes, one Gemini Enterprise app

This project bridges two complementary execution models that both register into
the **same** Gemini Enterprise app and both consume our enterprise MCP/proxy
tools and skills:

1. **agents-cli / ADK reasoning engine** (`app/agent.py`). A standard ADK agent
   deployed to **Agent Runtime** (a Vertex reasoning engine) or **Cloud Run**.
   This is what `agents-cli build/run/deploy` produces. Its tools are the same
   `make_enterprise_tools(...)` callables the gateway brokers — so the reasoning
   engine answers from enterprise connectors and applies/discovers skills
   through the control-plane proxy.

2. **Per-user managed agents** (the rest of this repo). Each user gets an
   isolated Antigravity **sandbox** managed through the Agents API, materialized
   per session by the gateway. These hold no credentials either; their proxy
   tools call back to the gateway exactly like the ADK agent's do.

Both surfaces register into one Gemini Enterprise engine: the ADK agent via
`agents-cli publish gemini-enterprise --registration-type a2a` pointed at the
gateway's A2A card, and the managed-agent control plane via the same A2A card
served at the gateway's well-known endpoint. One tool surface, one app, two
runtimes.

## Install and authenticate

```bash
uv tool install google-agents-cli
agents-cli login
```

## Inspect the project

```bash
agents-cli info   # reads agents-cli-manifest.yaml
```

This reports the project name (`antigravity-a2a-a2ui`), language (`python`),
agent directory (`app`), region (`us-east1`), base template (`adk_a2a`), and the
`create_params` (deployment target `cloud_run`, `is_a2a: true`, etc.).

## Run locally

```bash
agents-cli run   # loads app/agent.py -> root_agent / app
```

For the proxy tools to reach the gateway, set:

```bash
export A2A_GATEWAY_URL="https://<your-gateway>"      # control-plane base URL
export A2A_SESSION_TOKEN="<session-scoped proxy token>"   # or rely on the
                                                          # session file the
                                                          # gateway materializes
```

`build_tools()` resolves the token from `A2A_SESSION_TOKEN`, falling back to the
`session_token` field of the session file (path from `A2A_SESSION_FILE`, default
`.a2a/session.json`).

## Deploy the gateway to Cloud Run

The thing that gets published into Gemini Enterprise is the **gateway's A2A
card**, served by the deployed FastAPI service. Deploy the gateway (this repo's
`Dockerfile`) to Cloud Run, e.g.:

```bash
gcloud run deploy antigravity-a2a-gateway \
  --source . \
  --region us-east1 \
  --set-env-vars A2A_PUBLIC_URL=https://<service-url>,A2A_GE_PROJECT=<n>,A2A_GE_ENGINE=<app>
```

The compat router serves the adapted A2A card at:

- `https://<service-url>/a2a/app/.well-known/agent-card.json` (agents-cli path)
- `https://<service-url>/.well-known/agent-card.json` (bare A2A path)

Both adapt the gateway's source-of-truth card (`/.well-known/agent.json`) — no
duplicated card content.

## Publish to Gemini Enterprise

Generate the exact command from
`a2a_workspace.integrations.agents_cli.build_publish_command(...)`. With
`base_url=https://<service-url>` and
`app_engine_id=projects/<n>/locations/global/collections/default_collection/engines/<app>`:

```bash
agents-cli publish gemini-enterprise \
  --registration-type a2a \
  --agent-card-url https://<service-url>/a2a/app/.well-known/agent-card.json \
  --gemini-enterprise-app-id projects/<n>/locations/global/collections/default_collection/engines/<app> \
  --display-name "Antigravity Skill Assistant"
```

`agents-cli` fetches that card URL and registers the A2A agent with Discovery
Engine.

## IAM grant for A2A on Cloud Run

For Discovery Engine to invoke the A2A service on Cloud Run, grant
`roles/run.servicesInvoker` to the Discovery Engine service agent
(`discoveryengine_invoker_member(<PROJECT_NUMBER>)`):

```bash
gcloud run services add-iam-policy-binding antigravity-a2a-gateway \
  --region us-east1 \
  --member "serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role roles/run.servicesInvoker
```
