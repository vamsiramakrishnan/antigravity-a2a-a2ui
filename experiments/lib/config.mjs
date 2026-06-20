// Loads + validates experiment configuration from experiments/.env and the
// process environment (env wins over .env).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

function loadDotEnv() {
  const p = path.join(here, "..", ".env");
  if (!fs.existsSync(p)) return;
  for (const line of fs.readFileSync(p, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m && !(m[1] in process.env)) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
}

export function loadConfig() {
  loadDotEnv();
  const e = process.env;
  return {
    projectId: e.PROJECT_ID || "",
    location: e.LOCATION || "global",
    baseAgent: e.BASE_AGENT || "antigravity-preview-05-2026",
    bucket: (e.GCS_BUCKET || "").replace(/\/+$/, ""),
    prefixA: e.PREFIX_A || "workspaces/userA",
    prefixB: e.PREFIX_B || "workspaces/userB",
    skillResource: e.SKILL_RESOURCE_NAME || "",
    gatewayUrl: (e.GATEWAY_URL || "").replace(/\/+$/, ""),
    mcpToken: e.MCP_TOKEN || "",
    keep: e.KEEP === "1",
    timeoutMs: Number(e.TIMEOUT_MS || 180000),
  };
}

export function sourceUri(cfg, prefix) {
  return `${cfg.bucket}/${prefix.replace(/^\/+/, "").replace(/\/+$/, "")}/`;
}

export function requireKeys(cfg, keys) {
  const missing = keys.filter((k) => !cfg[k]);
  if (missing.length) {
    throw new Error(
      `Missing required config: ${missing.join(", ")} — set them in experiments/.env (copy .env.example) or export them.`,
    );
  }
}
