// Orchestrates the isolation experiments and writes results/report-*.md.
//   node run_all.mjs            # all probes
//   node run_all.mjs 01 03      # only the named probes
import { loadConfig, requireKeys, sourceUri } from "./lib/config.mjs";
import * as platform from "./lib/platform.mjs";
import { PROBE_IDS } from "./lib/config_names.mjs";
import { writeReport } from "./lib/report.mjs";

const PROBES = [
  ["01", "./probes/01_identity.mjs"],
  ["02", "./probes/02_two_agents.mjs"],
  ["03", "./probes/03_cross_tenant.mjs"],
  ["04", "./probes/04_controls.mjs"],
  ["05", "./probes/05_mcp_reachability.mjs"],
];

async function main() {
  const only = process.argv.slice(2);
  const cfg = loadConfig();
  requireKeys(cfg, ["projectId", "bucket"]);

  const client = platform.makeClient(cfg);
  const tracked = new Set();
  const ctx = {
    cfg,
    sourceUri: (prefix) => sourceUri(cfg, prefix),
    gcsSource: platform.gcsSource,
    skillSource: platform.skillSource,
    createAgent: (spec) => platform.createAgent(client, cfg, spec),
    runInteraction: (id, input) => platform.runInteraction(client, cfg, id, input),
    track: (id) => tracked.add(id),
  };

  const results = [];
  for (const [pid, modPath] of PROBES) {
    if (only.length && !only.includes(pid)) continue;
    process.stderr.write(`\n▶ running experiment ${pid} …\n`);
    try {
      const { default: run } = await import(modPath);
      const res = await run(ctx);
      results.push(res);
      process.stderr.write(`  ${pid} → ${res.status}\n`);
    } catch (e) {
      results.push({
        id: pid,
        title: `experiment ${pid}`,
        question: "(crashed before producing a result)",
        status: "error",
        interpretation: String(e?.stack || e),
        evidence: { error: String(e?.message || e) },
      });
      process.stderr.write(`  ${pid} → error: ${e?.message || e}\n`);
    }
  }

  if (!cfg.keep) {
    process.stderr.write(`\n🧹 cleaning up probe agents (set KEEP=1 to keep)…\n`);
    for (const id of tracked.size ? tracked : new Set(PROBE_IDS)) {
      const ok = await platform.deleteAgent(client, cfg, id);
      process.stderr.write(`  delete ${id}: ${ok ? "ok" : "skipped/failed"}\n`);
    }
  }

  const { jsonPath, mdPath } = writeReport(results, cfg);
  process.stderr.write(`\n✅ report written:\n  ${mdPath}\n  ${jsonPath}\n\nPaste the .md back to close the open questions.\n`);
}

main().catch((e) => {
  console.error("\nFATAL:", e?.message || e);
  process.exit(1);
});
