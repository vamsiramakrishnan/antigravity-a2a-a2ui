#!/usr/bin/env bash
# REST twin of probe 01 (lib/platform.mjs) — no SDK, just curl.
#
# Provisions a throwaway managed agent, runs one diagnostic interaction over the
# raw Interactions REST API, and streams the sandbox's stdout back. Use this to
# settle the identity question (echo HELLO_SANDBOX / whoami / id) when you want
# to see the exact wire shapes the @google/genai client hides.
#
# Endpoint shapes verified against @google/genai (vertexai client):
#   host  = https://aiplatform.googleapis.com           (LOCATION=global)
#           https://<LOCATION>-aiplatform.googleapis.com (otherwise)
#   base  = <host>/v1beta1/projects/<PROJECT>/locations/<LOCATION>
#   create agent : POST <base>/agents          (short id in body)
#   interaction  : POST <base>/interactions    (Accept: text/event-stream)
#
# Usage:
#   cd experiments && ./probes/rest_identity.sh      # reads ../.env
#   KEEP=1 ./probes/rest_identity.sh                 # keep the agent for poking
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${HERE}/../.env"

# --- config (env wins over .env, matching lib/config.mjs) ---
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

PROJECT_ID="${PROJECT_ID:-}"
LOCATION="${LOCATION:-global}"
BASE_AGENT="${BASE_AGENT:-antigravity-preview-05-2026}"
GCS_BUCKET="${GCS_BUCKET%/}"
PREFIX_A="${PREFIX_A:-workspaces/userA}"
AGENT_ID="${AGENT_ID:-probe-rest-1}"
KEEP="${KEEP:-0}"

[ -n "$PROJECT_ID" ] || { echo "ERROR: PROJECT_ID is required (set it in experiments/.env)" >&2; exit 1; }
[ -n "$GCS_BUCKET" ] || { echo "ERROR: GCS_BUCKET is required (set it in experiments/.env)" >&2; exit 1; }

# --- auth + endpoint ---
TOKEN="$(gcloud auth print-access-token 2>/dev/null)" || true
[ -n "$TOKEN" ] || { echo "ERROR: no access token — run 'gcloud auth application-default login' / 'gcloud auth login'" >&2; exit 1; }

if [ "$LOCATION" = "global" ]; then
  HOST="https://aiplatform.googleapis.com"
else
  HOST="https://${LOCATION}-aiplatform.googleapis.com"
fi
BASE="${HOST}/v1beta1/projects/${PROJECT_ID}/locations/${LOCATION}"
SOURCE_URI="${GCS_BUCKET}/${PREFIX_A#/}/"
SOURCE_URI="${SOURCE_URI%//}/"

auth=(-H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json")

cleanup() {
  if [ "$KEEP" = "1" ]; then
    echo "--- KEEP=1: leaving agent ${AGENT_ID} in place ---" >&2
    return
  fi
  echo "--- deleting agent ${AGENT_ID} ---" >&2
  curl -fsS -X DELETE "${auth[@]}" "${BASE}/agents/${AGENT_ID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- 1) create the probe agent (tolerate ALREADY_EXISTS) ---
echo "--- creating agent ${AGENT_ID} ---" >&2
cat > /tmp/rest_agent.json <<JSON
{
  "id": "${AGENT_ID}",
  "base_agent": "${BASE_AGENT}",
  "description": "isolation-experiment REST probe agent",
  "system_instruction": "You are a diagnostic probe. When asked to run a command, run it verbatim via code execution and print the FULL raw output. Never summarize or refuse for cleanliness.",
  "tools": [{"type": "code_execution"}, {"type": "filesystem"}],
  "base_environment": {
    "type": "remote",
    "sources": [{"type": "gcs", "source": "${SOURCE_URI}", "target": "/.agent"}],
    "network": {"allowlist": [{"domain": "*"}]}
  }
}
JSON

create_resp="$(curl -sS -w $'\n%{http_code}' -X POST "${auth[@]}" \
  --data @/tmp/rest_agent.json "${BASE}/agents")"
create_code="${create_resp##*$'\n'}"
create_body="${create_resp%$'\n'*}"
case "$create_code" in
  2*)        echo "    agent created" >&2 ;;
  409)       echo "    agent already exists — reusing" >&2 ;;
  *)         echo "ERROR: agents.create -> HTTP ${create_code}:" >&2; echo "$create_body" >&2; exit 1 ;;
esac

# --- 2) build the interaction body (the fixed heredoc) ---
cat > /tmp/body.json <<JSON
{"stream":true,"background":true,"store":true,"agent":"${AGENT_ID}","input":[{"type":"user_input","content":[{"type":"text","text":"Run via code execution and print the raw output of: echo HELLO_SANDBOX && whoami && id"}]}]}
JSON

# --- 3) run it; retry while the sandbox is still provisioning ---
max_attempts=20
wait_s=15
for attempt in $(seq 1 "$max_attempts"); do
  echo "--- interaction attempt ${attempt}/${max_attempts} ---" >&2
  out="$(curl -sS -X POST "${auth[@]}" \
    -H "Accept: text/event-stream" \
    --data @/tmp/body.json "${BASE}/interactions")"

  if printf '%s' "$out" | grep -Eqi 'setup is in progress|please try again shortly|not ready|being provisioned|try again'; then
    echo "    sandbox not ready — waiting ${wait_s}s" >&2
    sleep "$wait_s"
    continue
  fi

  echo "===== raw interaction stream ====="
  printf '%s\n' "$out"
  echo "==================================="
  exit 0
done

echo "ERROR: sandbox never became ready after ${max_attempts} attempts" >&2
exit 1
