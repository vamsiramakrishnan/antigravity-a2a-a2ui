// Aggregates probe results into results/report-<stamp>.{json,md}.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

function trunc(s, n = 4000) {
  s = String(s || "");
  return s.length > n ? `${s.slice(0, n)}\n…(${s.length - n} more chars)` : s;
}

function redact(cfg) {
  return { ...cfg, mcpToken: cfg.mcpToken ? "<set>" : "" };
}

function renderMd(results, cfg) {
  const L = [];
  L.push(`# Sandbox isolation experiment report`, ``);
  L.push(`- project: \`${cfg.projectId}\` · location: \`${cfg.location}\` · base_agent: \`${cfg.baseAgent}\``);
  L.push(`- bucket: \`${cfg.bucket}\` · prefixes: \`${cfg.prefixA}\` / \`${cfg.prefixB}\``);
  L.push(`- gateway: \`${cfg.gatewayUrl || "(not set)"}\` · generated: ${new Date().toISOString()}`, ``);
  L.push(`## Summary`, ``, `| # | Experiment | Status | One-line reading |`, `| --- | --- | --- | --- |`);
  for (const r of results) {
    L.push(`| ${r.id} | ${r.title} | **${r.status}** | ${(r.interpretation || "").split("\n")[0]} |`);
  }
  L.push(``);
  for (const r of results) {
    L.push(`## ${r.id} — ${r.title}`, ``);
    L.push(`**Question:** ${r.question}`, ``);
    L.push(`**Status:** \`${r.status}\``, ``);
    if (r.interpretation) L.push(`**Reading:** ${r.interpretation}`, ``);
    const ev = typeof r.evidence === "string" ? r.evidence : JSON.stringify(r.evidence, null, 2);
    L.push("```", trunc(ev), "```", ``);
  }
  L.push(`---`, `Paste this whole file back to close out the open isolation questions.`);
  return L.join("\n");
}

export function writeReport(results, cfg) {
  const outDir = path.join(here, "..", "results");
  fs.mkdirSync(outDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const jsonPath = path.join(outDir, `report-${stamp}.json`);
  const mdPath = path.join(outDir, `report-${stamp}.md`);
  fs.writeFileSync(jsonPath, JSON.stringify({ cfg: redact(cfg), results }, null, 2));
  fs.writeFileSync(mdPath, renderMd(results, cfg));
  return { jsonPath, mdPath };
}
