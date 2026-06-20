// EXPERIMENT 02 — Per-agent vs shared service account.
// Provision two agents (scoped to different prefixes) and compare the identity
// each sandbox reports. Same identity => shared SA (IAM alone can't isolate).
const INPUT = `Print FULL raw output:
TOK=$(gcloud auth print-access-token 2>/dev/null); if [ -n "$TOK" ]; then curl -s "https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=$TOK"; else echo NO_AMBIENT_TOKEN; fi
echo '--- metadata-sa ---'; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' 2>/dev/null || echo NO_METADATA`;

function ident(text) {
  const email = text.match(/"email"\s*:\s*"([^"]+)"/)?.[1] || text.match(/[\w.+-]+@[\w.-]+\.iam\.gserviceaccount\.com/)?.[0] || null;
  const azp = text.match(/"azp"\s*:\s*"([^"]+)"/)?.[1] || null;
  return { email, azp };
}

export default async function run(ctx) {
  const a = "probe-sa-a";
  const b = "probe-sa-b";
  ctx.track(a);
  ctx.track(b);
  const ca = await ctx.createAgent({ id: a, sources: [ctx.gcsSource(ctx.sourceUri(ctx.cfg.prefixA))] });
  const cb = await ctx.createAgent({ id: b, sources: [ctx.gcsSource(ctx.sourceUri(ctx.cfg.prefixB))] });
  if (!ca.ok || !cb.ok) return mk("error", `create failed: ${ca.error || ""} ${cb.error || ""}`, { ca, cb });

  const ra = await ctx.runInteraction(a, INPUT);
  const rb = await ctx.runInteraction(b, INPUT);
  const ia = ident(ra.text || "");
  const ib = ident(rb.text || "");

  let status = "inconclusive";
  let interp = "Could not extract an identity from both agents — inspect the two stdout blocks.";
  if (ia.email && ib.email) {
    if (ia.email === ib.email) {
      status = "shared-sa";
      interp =
        "Both sandboxes run as the SAME identity. Per-prefix IAM cannot isolate by itself — provisioner mount-correctness + our storage guard remain the real boundary.";
    } else {
      status = "per-agent-sa";
      interp =
        "Each sandbox has a DISTINCT identity. Per-agent IAM bindings genuinely isolate — we can trim our guard to belt-and-suspenders once experiment 03 confirms.";
    }
  } else if (/NO_AMBIENT_TOKEN/.test(ra.text || "") && /NO_AMBIENT_TOKEN/.test(rb.text || "")) {
    status = "no-ambient-creds";
    interp = "Neither sandbox exposed an identity (consistent with experiment 01 'no-ambient-creds').";
  }
  return mk(status, interp, {
    agentA: { id: a, identity: ia, stdout: (ra.text || "").slice(0, 1500) },
    agentB: { id: b, identity: ib, stdout: (rb.text || "").slice(0, 1500) },
  });

  function mk(s, i, ev) {
    return {
      id: "02",
      title: "Per-agent vs shared service account",
      question: "Do two managed agents run as distinct identities, or one shared SA?",
      status: s,
      interpretation: i,
      evidence: ev,
    };
  }
}
