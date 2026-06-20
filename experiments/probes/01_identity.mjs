// EXPERIMENT 01 — Sandbox runtime identity.
// Does the sandbox carry ambient credentials, and as whom does it read mounts?
const INPUT = `Run this and print the FULL raw output, do not summarize:
echo '--- whoami ---'; whoami; id
echo '--- gcloud token ---'; TOK=$(gcloud auth print-access-token 2>/dev/null); if [ -n "$TOK" ]; then echo HAVE_TOKEN; curl -s "https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=$TOK"; else echo NO_AMBIENT_TOKEN; fi
echo '--- metadata SA ---'; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' 2>/dev/null || echo NO_METADATA
echo '--- adc ---'; ls -la ~/.config/gcloud 2>/dev/null || echo NO_ADC`;

export default async function run(ctx) {
  const id = "probe-identity-a";
  ctx.track(id);
  const c = await ctx.createAgent({ id, sources: [ctx.gcsSource(ctx.sourceUri(ctx.cfg.prefixA))] });
  if (!c.ok) return mk("error", `agents.create failed: ${c.error}`, { error: c.error });

  const r = await ctx.runInteraction(id, INPUT);
  const t = r.text || "";
  if (!t && !r.ok) return mk("error", `interaction failed: ${r.error}`, { raw: r.raw, error: r.error });

  let status = "inconclusive";
  let interp = "Could not classify automatically — read the stdout below.";
  if (/HAVE_TOKEN/.test(t)) {
    status = "ambient-creds";
    interp =
      "Sandbox HAS an ambient token. Note its scope/azp/email below — that identity reads your mounts. Experiments 02/03 reveal whether it is per-agent and whether it crosses tenants.";
  } else if (/NO_AMBIENT_TOKEN/.test(t)) {
    status = "no-ambient-creds";
    interp =
      "No ambient access token. Strongest case: isolation does not rest on a sandbox identity; keep the storage guard as defense-in-depth and lean on the platform.";
  }
  return mk(status, interp, { stdout: t, sampleEvents: r.raw?.slice(0, 3) });

  function mk(s, i, ev) {
    return {
      id: "01",
      title: "Sandbox runtime identity",
      question: "Does the sandbox have ambient credentials, and as whom does it read mounted sources?",
      status: s,
      interpretation: i,
      evidence: ev,
    };
  }
}
