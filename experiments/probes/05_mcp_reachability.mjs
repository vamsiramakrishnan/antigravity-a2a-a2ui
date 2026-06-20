// EXPERIMENT 05 — MCP reachability (unblocks mounting the /mcp router).
// Provision an agent with an mcp_server tool pointing at the deployed gateway,
// and an allowlist that admits it. Then ask the agent to use the tool. Confirms
// (a) the sandbox can reach our gateway and (b) the bearer header survives.
// Requires GATEWAY_URL (and ideally MCP_TOKEN); otherwise skipped.
export default async function run(ctx) {
  const { cfg } = ctx;
  if (!cfg.gatewayUrl) {
    return mk("skipped", "GATEWAY_URL not set — deploy the gateway and set GATEWAY_URL (and MCP_TOKEN) to run this.", {});
  }
  const id = "probe-mcp";
  ctx.track(id);
  let domain = "";
  try {
    domain = new URL(cfg.gatewayUrl).host;
  } catch {
    return mk("error", `GATEWAY_URL is not a valid URL: ${cfg.gatewayUrl}`, {});
  }

  const mcpTool = {
    type: "mcp_server",
    name: "enterprise",
    url: `${cfg.gatewayUrl}/mcp`,
    headers: cfg.mcpToken ? { Authorization: `Bearer ${cfg.mcpToken}` } : {},
  };
  const c = await ctx.createAgent({
    id,
    sources: [ctx.gcsSource(ctx.sourceUri(cfg.prefixA))],
    tools: [{ type: "code_execution" }, { type: "filesystem" }, mcpTool],
    network: { allowlist: [{ domain }, { domain: "*.run.app" }] },
  });
  if (!c.ok) {
    return mk("error", `agents.create with mcp_server tool failed: ${c.error}`, {
      error: c.error,
      note: "If the error names the mcp_server field, the API may not accept HTTP MCP tools in this version.",
    });
  }

  const r = await ctx.runInteraction(
    id,
    "List the tools you have available (names only). Then call the 'enterprise' tool's tools/list capability if present and report exactly what it returns. If a tool call fails, print the raw error.",
  );
  const t = r.text || "";
  const sawTool = /enterprise|search_enterprise|find_enterprise_skills|tools\/list/i.test(t);
  const status = sawTool ? "tool-visible" : "inconclusive";
  const interp =
    (sawTool
      ? "The agent sees the enterprise MCP tool. "
      : "Could not confirm the tool from the transcript. ") +
    "Now confirm on the gateway side: check its logs for POST /mcp/tools/list or /mcp/tools/call with the Authorization header. Also note the session granularity (one stable session across turns, or independent interactions) — that decides whether the MCP header token should be per-user or per-interaction.";
  return mk(status, interp, { gateway: cfg.gatewayUrl, allowlistDomain: domain, transcript: t.slice(0, 2000) });

  function mk(s, i, ev) {
    return {
      id: "05",
      title: "MCP reachability",
      question: "Can the sandbox reach our /mcp gateway with the bearer header, and what is the session granularity?",
      status: s,
      interpretation: i,
      evidence: ev,
    };
  }
}
