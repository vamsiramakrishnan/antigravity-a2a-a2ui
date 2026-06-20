// scripts/probe_sandbox_identity.mjs
//
// WHY THIS EXISTS
// ---------------
// This settles the ONE open question in ADR 0001 (docs/adr/0001-...md, "The one
// open question — must be measured, not inferred"):
//
//   What identity does the Managed Agents sandbox harness use to read its
//   mounted sources?
//
// That answer decides whether "one agent per user + per-prefix IAM" is ENFORCED
// isolation, or whether the storage-adapter guard (GuardedStorageAdapter) stays
// the real boundary and per-agent IAM is only cosmetic.
//
// The platform turn carries no identity field (ADR 0001 #2), so we cannot read
// the answer from any API response — we must make the sandbox INTROSPECT ITS OWN
// AMBIENT CREDENTIALS and report them back. We do that by asking the agent to run
// a shell probe (whoami / id / gcloud token scopes / ADC / metadata SA email)
// inside the remote sandbox and stream the output back to us.
//
// HOW TO READ THE RESULT — three outcomes:
//
//   1. NO AMBIENT TOKEN  (the probe prints "NO AMBIENT TOKEN", no ADC, metadata
//      "none") → the sandbox has no GCP identity at all. Mounts are brokered FOR
//      it (the platform stages bytes); the runtime cannot reach Storage itself.
//      → Isolation is enforced upstream of the runtime. Best case.
//
//   2. SCOPED / DOWNSCOPED TOKEN  (tokeninfo shows a narrow scope, e.g. a single
//      devstorage scope, an expiry, and a principal that is per-agent /
//      short-lived) → the runtime holds a per-agent downscoped token. Per-agent
//      IAM + per-prefix scoping is the enforced boundary. Good case — verify the
//      scope is genuinely prefix-bound (CAB) and not bucket-wide.
//
//   3. BROAD SHARED SA  (metadata returns a long-lived service-account email and
//      tokeninfo shows cloud-platform scope shared across agents) → the runtime
//      runs as ONE shared, broadly-scoped service account. Per-agent IAM does NOT
//      isolate; the storage-adapter guard remains the real boundary, and ADR 2's
//      in-runtime policy (deny exfil patterns, workspace_only) is load-bearing.
//      Worst case — document and keep the guard.
//
// CANONICAL PATH. This @google/genai script is the canonical probe. A REST
// fallback for non-Node operators lives in scripts/probe_runtime_identity.py.
//
// USAGE:
//   cd scripts && npm install
//   PROJECT_ID=my-proj AGENT=my-existing-agent-id npm run probe:identity
//
// ENV VARS:
//   PROJECT_ID  (required)  GCP project id (or number) for Vertex AI.
//   LOCATION    (optional)  Vertex location. Default "global".
//   AGENT       (required)  Id of an EXISTING managed agent whose sandbox we
//                           probe. Provision one first with provision_and_run.mjs
//                           if you have none (it needs code_execution tool).

import { GoogleGenAI } from "@google/genai";

const PROJECT_ID = process.env.PROJECT_ID;
const LOCATION = process.env.LOCATION || "global";
const AGENT = process.env.AGENT;

function requireEnv(name, value) {
  if (!value) {
    console.error(
      `ERROR: missing required env var ${name}.\n` +
        `  PROJECT_ID (required), AGENT (required, an existing agent id), ` +
        `LOCATION (optional, default "global").`,
    );
    process.exit(2);
  }
}

requireEnv("PROJECT_ID", PROJECT_ID);
requireEnv("AGENT", AGENT);

// The introspection command. Single line so it survives as one shell input.
// It deliberately tries every place an ambient credential could hide:
//   whoami / id          → which OS principal the harness runs as
//   gcloud token + tokeninfo → scopes/expiry/principal of any ADC token
//   ~/.config/gcloud     → whether ADC files are mounted
//   metadata server      → the VM/instance service-account email (broad SA tell)
const PROBE_INPUT =
  "Run this and show the full output: " +
  "echo '--- whoami ---'; whoami; id; " +
  "echo '--- gcloud token scopes ---'; " +
  "TOK=$(gcloud auth print-access-token 2>/dev/null); " +
  'if [ -n "$TOK" ]; then ' +
  'curl -s "https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=$TOK"; ' +
  "else echo 'NO AMBIENT TOKEN'; fi; " +
  "echo '--- adc ---'; ls -la ~/.config/gcloud 2>/dev/null; " +
  "echo '--- metadata SA ---'; " +
  "curl -s -H 'Metadata-Flavor: Google' " +
  "'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' " +
  "2>/dev/null || echo none";

async function main() {
  console.error(
    `[probe] project=${PROJECT_ID} location=${LOCATION} agent=${AGENT}`,
  );
  console.error(`[probe] sending introspection turn; streaming events...\n`);

  const client = new GoogleGenAI({
    vertexai: true,
    project: PROJECT_ID,
    location: LOCATION,
  });

  // background:true + store:true so the turn is durable and we can re-read it;
  // environment.type "remote" forces the managed sandbox (the thing we probe).
  const stream = await client.interactions.create({
    agent: AGENT,
    input: PROBE_INPUT,
    environment: { type: "remote" },
    stream: true,
    background: true,
    store: true,
  });

  let eventCount = 0;
  for await (const event of stream) {
    eventCount += 1;
    // Print every event verbatim — we do not know which event type carries the
    // shell output, so dump them all and let the operator scan for the markers
    // (--- whoami ---, NO AMBIENT TOKEN, the SA email, etc.).
    try {
      console.log(JSON.stringify(event));
    } catch {
      console.log(String(event));
    }
  }

  console.error(`\n[probe] stream ended after ${eventCount} event(s).`);
  console.error(
    `[probe] Interpret against the three outcomes in this file's header:\n` +
      `  NO AMBIENT TOKEN -> isolation enforced upstream (best)\n` +
      `  scoped/downscoped token -> per-agent IAM is the boundary (good)\n` +
      `  broad shared SA email -> storage-guard + in-runtime policy is the boundary (worst)`,
  );
}

main().catch((err) => {
  console.error("[probe] FAILED:", err?.stack || err);
  process.exit(1);
});
