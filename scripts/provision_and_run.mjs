// scripts/provision_and_run.mjs
//
// WHY THIS EXISTS
// ---------------
// End-to-end smoke test for the "per-user managed agent" model that ADR 0001
// adopts. It exercises both planes the control plane will wrap:
//
//   Phase A — Agent Setup plane (Agents API / control plane):
//             idempotent agents.create with a base_environment whose sources[]
//             are scoped to THIS user (a GCS prefix, optionally a Skill Registry
//             entry). This is where ADR 0001 says "Isolation is enforced here".
//
//   Phase B — Usage plane (Interactions API / data plane):
//             interactions.create against that agent and stream the result.
//
// Provisioning is GUARDED: we agents.get first and skip create if it already
// exists, so re-running is safe (matches provisioning/'s "idempotent first-touch"
// contract).
//
// USAGE:
//   cd scripts && npm install
//   PROJECT_ID=my-proj AGENT_ID=user-abc123 GCS_BUCKET=gs://my-bucket/users/abc123 \
//     INPUT="List the files under /.agent and summarize any SKILL.md" \
//     npm run provision
//
//   # To demonstrate skill mounting, also set:
//   SKILL_RESOURCE_NAME=projects/P/locations/global/collections/.../skills/my-skill
//
// ENV VARS:
//   PROJECT_ID          (required)  GCP project id/number.
//   LOCATION            (optional)  Vertex location. Default "global".
//   AGENT_ID            (required)  Stable per-user agent id (e.g. derived from
//                                   the verified (iss,sub) -> workspace uuid).
//   GCS_BUCKET          (required)  Per-user GCS source, e.g.
//                                   gs://bucket/users/<uuid>. Mounted at /.agent.
//   BASE_AGENT          (optional)  Base harness. Default
//                                   "antigravity-preview-05-2026".
//   AGENT_DESCRIPTION   (optional)  Human description for the stored config.
//   SYSTEM_INSTRUCTION  (optional)  System instruction for the agent.
//   INPUT               (optional)  The interaction prompt. Has a safe default.
//   SKILL_RESOURCE_NAME (optional)  Skill Registry resource name. When set, adds
//                                   a {type:"skill_registry"} source mounted at
//                                   /.agent/skills — demonstrates skill mounting.
//   SKIP_INTERACTION    (optional)  "1" to provision only, no interaction.

import { GoogleGenAI } from "@google/genai";

const PROJECT_ID = process.env.PROJECT_ID;
const LOCATION = process.env.LOCATION || "global";
const AGENT_ID = process.env.AGENT_ID;
const GCS_BUCKET = process.env.GCS_BUCKET;
const BASE_AGENT = process.env.BASE_AGENT || "antigravity-preview-05-2026";
const AGENT_DESCRIPTION =
  process.env.AGENT_DESCRIPTION ||
  `Per-user managed agent ${AGENT_ID} (provisioned by provision_and_run.mjs)`;
const SYSTEM_INSTRUCTION =
  process.env.SYSTEM_INSTRUCTION ||
  "You are a per-user workspace agent. Your skills and files are mounted under " +
    "/.agent. Operate only within the mounted workspace.";
const INPUT =
  process.env.INPUT ||
  "List the files under /.agent (recursively, one level into subdirs) and " +
    "summarize any SKILL.md you find.";
const SKILL_RESOURCE_NAME = process.env.SKILL_RESOURCE_NAME;
const SKIP_INTERACTION = process.env.SKIP_INTERACTION === "1";

function requireEnv(name, value) {
  if (!value) {
    console.error(
      `ERROR: missing required env var ${name}.\n` +
        `  required: PROJECT_ID, AGENT_ID, GCS_BUCKET.\n` +
        `  optional: LOCATION, BASE_AGENT, AGENT_DESCRIPTION, ` +
        `SYSTEM_INSTRUCTION, INPUT, SKILL_RESOURCE_NAME, SKIP_INTERACTION.`,
    );
    process.exit(2);
  }
}

requireEnv("PROJECT_ID", PROJECT_ID);
requireEnv("AGENT_ID", AGENT_ID);
requireEnv("GCS_BUCKET", GCS_BUCKET);

// Per-user sources. The GCS prefix is the user's workspace; mounting it at
// /.agent matches the sandbox layout ADR 0001 #5 verified.
const sources = [{ type: "gcs", source: GCS_BUCKET, target: "/.agent" }];

// Optional skill mount. Skill Registry skills carry a native sha256 /
// SkillRevision (ADR 0001 #5) and land in the agentskills.io layout we emit.
if (SKILL_RESOURCE_NAME) {
  sources.push({
    type: "skill_registry",
    source: SKILL_RESOURCE_NAME,
    target: "/.agent/skills",
  });
}

const agentBody = {
  id: AGENT_ID,
  base_agent: BASE_AGENT,
  description: AGENT_DESCRIPTION,
  system_instruction: SYSTEM_INSTRUCTION,
  tools: [
    { type: "code_execution" },
    { type: "filesystem" },
    { type: "google_search" },
  ],
  base_environment: {
    type: "remote",
    sources,
    // NOTE: "*" is for smoke-testing only. In production the control plane pins
    // a narrow network.allowlist per agent (ADR 0002 §4 gates SEARCH_WEB).
    network: { allowlist: [{ domain: "*" }] },
  },
};

async function ensureAgent(client) {
  // GUARD: get first; only create if absent. Idempotent re-runs.
  try {
    const existing = await client.agents.get({ agent: AGENT_ID });
    console.error(`[provision] agent "${AGENT_ID}" already exists; skipping create.`);
    return existing;
  } catch (err) {
    console.error(
      `[provision] agents.get("${AGENT_ID}") missed (${err?.message || err}); creating...`,
    );
  }

  const created = await client.agents.create(agentBody);
  console.error(`[provision] created agent "${AGENT_ID}".`);
  console.error(
    `[provision] sources: ` +
      sources.map((s) => `${s.type}:${s.source}->${s.target}`).join(", "),
  );
  return created;
}

async function runInteraction(client) {
  console.error(`[run] interactions.create on agent "${AGENT_ID}"; streaming...\n`);
  const stream = await client.interactions.create({
    agent: AGENT_ID,
    input: INPUT,
    environment: { type: "remote" },
    stream: true,
    store: true,
  });

  let eventCount = 0;
  for await (const event of stream) {
    eventCount += 1;
    try {
      console.log(JSON.stringify(event));
    } catch {
      console.log(String(event));
    }
  }
  console.error(`\n[run] stream ended after ${eventCount} event(s).`);
}

async function main() {
  console.error(
    `[provision] project=${PROJECT_ID} location=${LOCATION} ` +
      `agent=${AGENT_ID} base_agent=${BASE_AGENT}`,
  );

  const client = new GoogleGenAI({
    vertexai: true,
    project: PROJECT_ID,
    location: LOCATION,
  });

  await ensureAgent(client);

  if (SKIP_INTERACTION) {
    console.error("[run] SKIP_INTERACTION=1; provision-only, exiting.");
    return;
  }

  await runInteraction(client);
}

main().catch((err) => {
  console.error("[provision_and_run] FAILED:", err?.stack || err);
  process.exit(1);
});
