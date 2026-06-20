// Deletes every probe agent the harness may have created. Safe to re-run.
import { loadConfig, requireKeys } from "./lib/config.mjs";
import * as platform from "./lib/platform.mjs";
import { PROBE_IDS } from "./lib/config_names.mjs";

const cfg = loadConfig();
requireKeys(cfg, ["projectId"]);
const client = platform.makeClient(cfg);

for (const id of PROBE_IDS) {
  const ok = await platform.deleteAgent(client, cfg, id);
  process.stderr.write(`delete ${id}: ${ok ? "ok" : "skipped/failed"}\n`);
}
