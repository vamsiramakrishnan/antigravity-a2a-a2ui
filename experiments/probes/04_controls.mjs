// EXPERIMENT 04 — Which hardening controls does the managed Agents API accept,
// and are they actually enforced in the sandbox?
//   (a) capabilities/policies fields: does agents.create accept them?
//   (b) network.allowlist: is a narrow allowlist enforced at runtime?
export default async function run(ctx) {
  const findings = {};

  // (a) Try to create an agent with capabilities (disable RUN_COMMAND) + a deny policy.
  const capsId = "probe-controls-caps";
  ctx.track(capsId);
  const caps = await ctx.createAgent({
    id: capsId,
    sources: [ctx.gcsSource(ctx.sourceUri(ctx.cfg.prefixA))],
    capabilities: { disabled_tools: ["RUN_COMMAND"], enable_subagents: false },
    policies: [{ tool: "run_command", decision: "DENY" }],
  });
  findings.capabilitiesPolicies = caps.ok
    ? { accepted: true, note: "agents.create did NOT reject capabilities/policies fields", extraFieldsSent: caps.extraFieldsSent }
    : { accepted: false, error: caps.error, note: "agents.create rejected the request when capabilities/policies were present" };

  if (caps.ok) {
    const r = await ctx.runInteraction(capsId, "Run this via shell and print output: echo HARDENING_SHELL_RAN");
    const ran = /HARDENING_SHELL_RAN/.test(r.text || "");
    findings.runCommandBehaviour = ran
      ? "shell STILL ran despite disabled_tools/deny — control not enforced (or field ignored)"
      : "shell did NOT run — RUN_COMMAND appears disabled/denied";
    findings.runCommandStdout = (r.text || "").slice(0, 800);
  }

  // (b) Narrow network allowlist — can the agent still reach a non-allowlisted host / metadata?
  const netId = "probe-controls-net";
  ctx.track(netId);
  const net = await ctx.createAgent({
    id: netId,
    sources: [ctx.gcsSource(ctx.sourceUri(ctx.cfg.prefixA))],
    network: { allowlist: [{ domain: "example.com" }] },
  });
  if (net.ok) {
    const r = await ctx.runInteraction(
      netId,
      `Print FULL raw output:
echo '--- non-allowlisted host ---'; curl -s -m 10 -o /dev/null -w '%{http_code}' https://www.google.com 2>&1 || echo CURL_FAILED
echo; echo '--- metadata server ---'; curl -s -m 10 -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' 2>&1 || echo META_BLOCKED`,
    );
    const t = r.text || "";
    const reachedGoogle = /\b200\b|\b30\d\b/.test(t) && !/CURL_FAILED/.test(t);
    const reachedMeta = /iam\.gserviceaccount\.com/.test(t);
    findings.networkAllowlist = {
      enforced: !reachedGoogle && !reachedMeta,
      reachedNonAllowlistedHost: reachedGoogle,
      reachedMetadataServer: reachedMeta,
      stdout: t.slice(0, 800),
    };
  } else {
    findings.networkAllowlist = { error: net.error };
  }

  const status = findings.capabilitiesPolicies.accepted ? "fields-accepted" : "fields-rejected";
  const interp =
    `agents.create ${findings.capabilitiesPolicies.accepted ? "ACCEPTED" : "REJECTED"} capabilities/policies. ` +
    `If rejected, our SDK hardening only applies to a self-hosted harness; in managed mode rely on network.allowlist + tool selection (see findings).`;

  return {
    id: "04",
    title: "Managed-agent hardening controls",
    question: "Does the Agents API accept capabilities/policies, and is network.allowlist enforced in the sandbox?",
    status,
    interpretation: interp,
    evidence: findings,
  };
}
