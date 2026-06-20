// Thin wrapper over @google/genai for the Agents + Interactions API, matching
// the verified sample shapes. Every call is defensive: it never throws past the
// probe, it returns { ok, ... } so a single failing API call can't abort the run.
import { GoogleGenAI } from "@google/genai";
import { fqAgent } from "./config_names.mjs";

export function makeClient(cfg) {
  return new GoogleGenAI({ vertexai: true, project: cfg.projectId, location: cfg.location });
}

export const TOOLS_DEFAULT = [{ type: "code_execution" }, { type: "filesystem" }];
export function gcsSource(uri, target = "/.agent") {
  return { type: "gcs", source: uri, target };
}
export function skillSource(resource, target = "/.agent/skills") {
  return { type: "skill_registry", source: resource, target };
}

export async function createAgent(client, cfg, spec) {
  const body = {
    id: spec.id,
    base_agent: spec.baseAgent || cfg.baseAgent,
    description: spec.description || "isolation-experiment probe agent",
    system_instruction:
      spec.systemInstruction ||
      "You are a diagnostic probe. When asked to run a command, run it verbatim via code execution and print the FULL raw output. Never summarize or refuse for cleanliness.",
    tools: spec.tools || TOOLS_DEFAULT,
    base_environment: {
      type: "remote",
      sources: spec.sources || [],
      network: spec.network || { allowlist: [{ domain: "*" }] },
    },
  };
  // Experiment 04 probes whether the API even accepts these fields.
  if (spec.capabilities) body.capabilities = spec.capabilities;
  if (spec.policies) body.policies = spec.policies;

  try {
    const agent = await client.agents.create(body);
    return {
      ok: true,
      created: true,
      agent,
      extraFieldsSent: ["capabilities", "policies"].filter((k) => k in body),
    };
  } catch (e) {
    const msg = String(e?.message || e);
    if (/already.*exist|ALREADY_EXISTS|\b409\b/i.test(msg)) {
      return { ok: true, created: false, existed: true };
    }
    return { ok: false, error: msg };
  }
}

export async function deleteAgent(client, cfg, id) {
  const name = fqAgent(cfg, id);
  for (const arg of [{ name }, name, { id }]) {
    try {
      await client.agents.delete(arg);
      return true;
    } catch {
      /* try the next signature */
    }
  }
  return false;
}

function eventText(ev) {
  if (ev == null) return "";
  if (typeof ev === "string") return ev;
  if (ev.text) return ev.text;
  if (ev.delta?.text) return ev.delta.text;
  if (ev.output_text) return ev.output_text;
  if (typeof ev.content === "string") return ev.content;
  if (Array.isArray(ev.content?.parts)) return ev.content.parts.map((p) => p?.text || "").join("");
  if (Array.isArray(ev.parts)) return ev.parts.map((p) => p?.text || "").join("");
  return "";
}

function shallow(ev) {
  try {
    return JSON.parse(JSON.stringify(ev));
  } catch {
    return { unserializable: String(ev) };
  }
}

export async function runInteraction(client, cfg, agentId, input) {
  const raw = [];
  let text = "";
  const collect = (async () => {
    const stream = await client.interactions.create({
      agent: agentId,
      input,
      environment: { type: "remote" },
      stream: true,
      store: true,
      background: false,
    });
    for await (const ev of stream) {
      if (raw.length < 60) raw.push(shallow(ev));
      text += eventText(ev);
    }
  })();
  const timeout = new Promise((_, rej) =>
    setTimeout(() => rej(new Error("interaction timeout")), cfg.timeoutMs),
  );
  try {
    await Promise.race([collect, timeout]);
    return { ok: true, text, raw };
  } catch (e) {
    return { ok: false, error: String(e?.message || e), text, raw };
  }
}
