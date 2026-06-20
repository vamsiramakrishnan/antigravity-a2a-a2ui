// Shared agent-id naming so the runner and cleanup agree.
export function fqAgent(cfg, id) {
  return `projects/${cfg.projectId}/locations/${cfg.location}/agents/${id}`;
}

// Every probe agent the harness may create — used by cleanup.
export const PROBE_IDS = [
  "probe-identity-a",
  "probe-sa-a",
  "probe-sa-b",
  "probe-iso-a",
  "probe-controls-caps",
  "probe-controls-net",
  "probe-mcp",
];
