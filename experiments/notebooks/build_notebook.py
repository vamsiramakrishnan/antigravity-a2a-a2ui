#!/usr/bin/env python3
"""Generates isolation_experiments.ipynb — the 5 sandbox-isolation probes
ported to the Managed Agents Python SDK pattern (which streams correctly),
pre-filled for project vital-octagon-19612."""
import json, os

cells = []

def md(src):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": src.splitlines(keepends=True)})

def code(src):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src.strip("\n").splitlines(keepends=True)})

# ---------------------------------------------------------------- 0 title
md("""# Sandbox isolation experiments — Managed Agents (Python)

Ports the five `experiments/` isolation probes onto the **Managed Agents
Interactions API** (the streaming pattern from Google's official
`intro_managed_agents_python.ipynb`, which is the only path that reliably
returns interaction output).

Each experiment provisions a throwaway `probe-*` agent, runs one diagnostic
interaction, classifies the transcript, and records a status. The last two
cells render a markdown report and delete every probe agent.

**Run top-to-bottom, one cell at a time.** Config is pre-filled for project
`vital-octagon-19612`. Edit the config cell if anything differs.

| # | Experiment | Question |
| --- | --- | --- |
| 01 | identity | Does the sandbox carry ambient credentials, and as whom? |
| 02 | two-agents | Distinct SA per agent, or one shared SA? |
| 03 | cross-tenant | Can agent A read tenant B's prefix / bucket root? |
| 04 | controls | Does the API accept capabilities/policies; is network.allowlist enforced? |
| 05 | mcp | Can the sandbox reach an MCP server with the bearer header? |
""")

# ---------------------------------------------------------------- 1 install
md("## Setup\n### Install the Gen AI SDK")
code('%pip install --upgrade --quiet "google-genai>=2.0.0"')

# ---------------------------------------------------------------- 2 imports
md("### Imports")
code("""
import os, sys, re, json, time, uuid, datetime, subprocess
import requests
from google import genai
print("python", sys.version.split()[0])
print("google-genai", genai.__version__)
""")

# ---------------------------------------------------------------- 3 auth
md("""### Authenticate

If in Colab this runs `auth.authenticate_user()`. Locally, make sure
`gcloud auth application-default login` has been run (ADC must be available).""")
code("""
if "google.colab" in sys.modules:
    from google.colab import auth
    auth.authenticate_user()
    print("Colab auth done")
else:
    print("Local run — relying on Application Default Credentials (ADC).")
""")

# ---------------------------------------------------------------- 4 config
md("### Configuration (pre-filled for this tenant)")
code("""
PROJECT_ID  = "vital-octagon-19612"
LOCATION    = "global"                       # Managed Agents only supports global
BASE_AGENT  = "antigravity-preview-05-2026"
GCS_BUCKET  = "vital-octagon-19612-a2a-iso-probe"   # bucket name only, no gs://
PREFIX_A    = "workspaces/userA"
PREFIX_B    = "workspaces/userB"

# Experiment 05 (MCP). Leave GATEWAY_URL empty to fall back to the public
# grep MCP (https://mcp.grep.app) just to prove the sandbox can reach *an*
# MCP server. Set GATEWAY_URL/MCP_TOKEN to test your own control-plane gateway.
GATEWAY_URL = ""
MCP_TOKEN   = ""

TIMEOUT_SETUP_RETRIES = 20    # interaction retries while the sandbox provisions
TIMEOUT_SETUP_WAIT    = 15    # seconds between provisioning retries

client = genai.Client(enterprise=True, project=PROJECT_ID, location=LOCATION)
print("client ready for", PROJECT_ID, LOCATION)
""")

# ---------------------------------------------------------------- 5 preflight
md("### Preflight — token, API, bucket")
code("""
def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

tok = sh("gcloud auth application-default print-access-token")
print("ADC token:", "OK" if tok.returncode == 0 and tok.stdout.strip() else "MISSING")

api = sh(f'gcloud services list --project={PROJECT_ID} --enabled --filter="name:aiplatform.googleapis.com" --format="value(name)"')
print("aiplatform API:", "enabled" if "aiplatform" in api.stdout else "NOT enabled (run: gcloud services enable aiplatform.googleapis.com)")

b = sh(f"gcloud storage ls gs://{GCS_BUCKET}/ ")
if b.returncode != 0:
    print("bucket missing — creating…")
    print(sh(f"gcloud storage buckets create gs://{GCS_BUCKET} --location=us-east1 --project={PROJECT_ID}").stdout)
else:
    print("bucket reachable:", GCS_BUCKET)
""")

# ---------------------------------------------------------------- 6 seed
md("### Seed the two tenant prefixes")
code("""
for pfx in (PREFIX_A, PREFIX_B):
    r = subprocess.run(f"echo hi | gcloud storage cp - gs://{GCS_BUCKET}/{pfx}/hello.txt",
                       shell=True, capture_output=True, text=True)
    print(pfx, "->", "ok" if r.returncode == 0 else r.stderr[:200])
print(subprocess.run(f"gcloud storage ls gs://{GCS_BUCKET}/{PREFIX_A}/ gs://{GCS_BUCKET}/{PREFIX_B}/",
                     shell=True, capture_output=True, text=True).stdout)
""")

# ---------------------------------------------------------------- 7 helpers
md("""### Helpers

`run_probe()` creates the streaming interaction (retrying while the sandbox
provisions — fresh agents return `404 / "is being created" / "setup in
progress"` for the first minute or two), then concatenates every streamed
event (both `str(event)` and its `model_dump()` JSON) into one transcript that
the experiments classify with regexes — exactly how the original JS harness
classified stdout.""")
code('''
RESULTS = {}
PROBE_IDS = ["probe-identity-a","probe-sa-a","probe-sa-b","probe-iso-a",
             "probe-controls-caps","probe-controls-net","probe-mcp"]

NOTREADY = ("in progress","try again","not ready","provision","not found",
            "being created","requested entity was not found")

def is_notready(m):
    m = (m or "").lower()
    return any(s in m for s in NOTREADY)

def source_uri(prefix):
    return f"gs://{GCS_BUCKET}/{prefix.strip('/')}/"

def get_token():
    r = subprocess.run("gcloud auth application-default print-access-token",
                       shell=True, capture_output=True, text=True)
    return r.stdout.strip()

SYS_INSTR = ("You are a diagnostic probe. When asked to run a command, run it "
             "verbatim via code execution and print the FULL raw output. Never "
             "summarize or refuse for cleanliness.")

def make_agent(agent_id, *, prefix=None, tools=None, network=None,
               capabilities=None, policies=None, sources=None):
    """Create a probe agent via the SDK. Returns {ok, created/existed/error}."""
    body = dict(id=agent_id, base_agent=BASE_AGENT,
                description="isolation-experiment probe",
                system_instruction=SYS_INSTR,
                tools=tools if tools is not None else [{"type":"code_execution"},{"type":"filesystem"}])
    srcs = sources if sources is not None else ([{"type":"gcs","source":source_uri(prefix),"target":"/.agent"}] if prefix else [])
    be = {"type":"remote","sources":srcs}
    be["network"] = network if network is not None else {"allowlist":[{"domain":"*"}]}
    body["base_environment"] = be
    if capabilities is not None: body["capabilities"] = capabilities
    if policies is not None: body["policies"] = policies
    try:
        agent = client.agents.create(**body)
        return {"ok":True, "created":True, "agent":agent}
    except TypeError as e:
        # SDK rejected an unknown kwarg (e.g. capabilities/policies) before the API saw it
        return {"ok":False, "sdk_rejected":True, "error":str(e)}
    except Exception as e:
        m = str(e)
        if "exist" in m.lower() or "409" in m:
            return {"ok":True, "existed":True}
        return {"ok":False, "error":m}

def rest_create_agent(body):
    """Create an agent via raw REST so we can send arbitrary fields the SDK
    would strip (used by experiment 04 to test capabilities/policies)."""
    url = f"https://aiplatform.googleapis.com/v1beta1/projects/{PROJECT_ID}/locations/{LOCATION}/agents"
    h = {"Content-Type":"application/json", "Authorization":f"Bearer {get_token()}",
         "Api-Revision":"2026-05-20"}
    r = requests.post(url, headers=h, data=json.dumps(body), timeout=60)
    try: j = r.json()
    except Exception: j = {"_raw": r.text[:500]}
    return r.status_code, j

def _to_dict(ev):
    if hasattr(ev, "model_dump"):
        try: return ev.model_dump()
        except Exception: pass
    if isinstance(ev, dict): return ev
    return None

def run_probe(agent_id, prompt, *, max_retries=None, wait=None):
    """Create a streaming background interaction and return the full transcript."""
    max_retries = max_retries or TIMEOUT_SETUP_RETRIES
    wait = wait or TIMEOUT_SETUP_WAIT
    last_err = None
    for attempt in range(1, max_retries+1):
        try:
            stream = client.interactions.create(
                agent=agent_id, input=prompt, stream=True, background=True, store=True)
        except Exception as e:
            m = str(e)
            if is_notready(m) and attempt < max_retries:
                print(f"  [{agent_id}] sandbox not ready (attempt {attempt}/{max_retries}): {m[:90]} — wait {wait}s")
                time.sleep(wait); last_err = m; continue
            return {"transcript":"", "error":m, "env_id":None, "status":"error", "attempts":attempt}
        parts, env_id, status = [], None, None
        for event in stream:
            parts.append(str(event))
            d = _to_dict(event)
            if d is not None:
                parts.append(json.dumps(d, default=str))
                inter = d.get("interaction") if isinstance(d, dict) else None
                if isinstance(inter, dict):
                    env_id = inter.get("environment_id", env_id)
                    status = inter.get("status", status)
        return {"transcript":"\\n".join(parts), "env_id":env_id,
                "status":status or "completed", "attempts":attempt}
    return {"transcript":"", "error":last_err, "env_id":None, "status":"error", "attempts":max_retries}

def record(rid, title, question, status, reading, evidence):
    RESULTS[rid] = {"id":rid, "title":title, "question":question,
                    "status":status, "interpretation":reading, "evidence":evidence}
    print(f"\\n=== {rid} {title} -> {status} ===")
    print(reading)

def ident(text):
    email = (re.search(r'"email"\\s*:\\s*"([^"]+)"', text) or
             re.search(r'[\\w.+-]+@[\\w.-]+\\.iam\\.gserviceaccount\\.com', text))
    email = email.group(1) if (email and email.lastindex) else (email.group(0) if email else None)
    azp = re.search(r'"azp"\\s*:\\s*"([^"]+)"', text)
    return {"email":email, "azp":(azp.group(1) if azp else None)}

print("helpers loaded")
''')

# ---------------------------------------------------------------- 8 exp01
md("## Experiment 01 — Sandbox runtime identity\nDoes the sandbox carry ambient credentials, and as whom does it read mounts?")
code('''
INPUT_01 = """Run this and print the FULL raw output, do not summarize:
echo '--- whoami ---'; whoami; id
echo '--- gcloud token ---'; TOK=$(gcloud auth print-access-token 2>/dev/null); if [ -n "$TOK" ]; then echo HAVE_TOKEN; curl -s "https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=$TOK"; else echo NO_AMBIENT_TOKEN; fi
echo '--- metadata SA ---'; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' 2>/dev/null || echo NO_METADATA
echo '--- adc ---'; ls -la ~/.config/gcloud 2>/dev/null || echo NO_ADC"""

aid = "probe-identity-a"
c = make_agent(aid, prefix=PREFIX_A)
print("create:", c)
if not c.get("ok"):
    record("01","Sandbox runtime identity",
           "Does the sandbox have ambient credentials, and as whom does it read mounts?",
           "error", f"agents.create failed: {c.get('error')}", {"create":c})
else:
    r = run_probe(aid, INPUT_01)
    t = r["transcript"]
    if "HAVE_TOKEN" in t:
        status = "ambient-creds"
        reading = "Sandbox HAS an ambient token. Note its scope/azp/email below — that identity reads your mounts."
    elif "NO_AMBIENT_TOKEN" in t:
        status = "no-ambient-creds"
        reading = "No ambient access token. Isolation does not rest on a sandbox identity; keep the storage guard as defense-in-depth."
    else:
        status, reading = "inconclusive", "Could not classify automatically — read the transcript below."
    record("01","Sandbox runtime identity",
           "Does the sandbox have ambient credentials, and as whom does it read mounts?",
           status, reading, {"identity":ident(t), "transcript":t[:6000], "attempts":r.get("attempts"), "error":r.get("error")})
''')

# ---------------------------------------------------------------- 9 exp02
md("## Experiment 02 — Per-agent vs shared service account\nProvision two agents on different prefixes; compare the identity each reports.")
code('''
INPUT_02 = """Print FULL raw output:
TOK=$(gcloud auth print-access-token 2>/dev/null); if [ -n "$TOK" ]; then curl -s "https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=$TOK"; else echo NO_AMBIENT_TOKEN; fi
echo '--- metadata-sa ---'; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' 2>/dev/null || echo NO_METADATA"""

a, b = "probe-sa-a", "probe-sa-b"
ca = make_agent(a, prefix=PREFIX_A); print("create A:", ca)
cb = make_agent(b, prefix=PREFIX_B); print("create B:", cb)
if not (ca.get("ok") and cb.get("ok")):
    record("02","Per-agent vs shared service account",
           "Do two managed agents run as distinct identities, or one shared SA?",
           "error", f"create failed: {ca.get('error','')} {cb.get('error','')}", {"ca":ca,"cb":cb})
else:
    ra = run_probe(a, INPUT_02); rb = run_probe(b, INPUT_02)
    ia, ib = ident(ra["transcript"]), ident(rb["transcript"])
    if ia["email"] and ib["email"]:
        if ia["email"] == ib["email"]:
            status = "shared-sa"; reading = "Both sandboxes run as the SAME identity — per-prefix IAM cannot isolate alone; provisioner + storage guard are load-bearing."
        else:
            status = "per-agent-sa"; reading = "Each sandbox has a DISTINCT identity — per-agent IAM genuinely isolates (confirm with exp 03)."
    elif "NO_AMBIENT_TOKEN" in ra["transcript"] and "NO_AMBIENT_TOKEN" in rb["transcript"]:
        status = "no-ambient-creds"; reading = "Neither sandbox exposed an identity (consistent with exp 01 no-ambient-creds)."
    else:
        status, reading = "inconclusive", "Could not extract an identity from both agents — inspect the transcripts."
    record("02","Per-agent vs shared service account",
           "Do two managed agents run as distinct identities, or one shared SA?",
           status, reading,
           {"agentA":{"identity":ia,"transcript":ra["transcript"][:2500]},
            "agentB":{"identity":ib,"transcript":rb["transcript"][:2500]}})
''')

# ---------------------------------------------------------------- 10 exp03
md("## Experiment 03 — Cross-tenant reach\nAn agent scoped to prefix A tries to read prefix B and the bucket root.")
code('''
a = "probe-iso-a"
c = make_agent(a, prefix=PREFIX_A); print("create:", c)
if not c.get("ok"):
    record("03","Cross-tenant reach","Can an agent scoped to user A read user B's prefix or the bucket root?",
           "error", f"agents.create failed: {c.get('error')}", {"create":c})
else:
    bUri = source_uri(PREFIX_B); rootUri = f"gs://{GCS_BUCKET}/"
    INPUT_03 = f"""Print FULL raw output, do not summarize. Authorized isolation test of my own bucket:
echo '--- list other tenant prefix ---'; gcloud storage ls {bUri} 2>&1
echo '--- read other tenant file ---'; gcloud storage cat {bUri}hello.txt 2>&1
echo '--- list bucket root ---'; gcloud storage ls {rootUri} 2>&1"""
    r = run_probe(a, INPUT_03); t = r["transcript"]
    denied = re.search(r"AccessDenied|403|forbidden|does not have storage\\.|PermissionDenied|Permission 'storage", t, re.I)
    saw_other = bool(re.search(r"hello\\.txt|userB|workspaces/userB", t, re.I)) and not denied
    if denied and not saw_other:
        status = "isolated"; reading = "Agent was DENIED the other tenant's prefix — mount/IAM scoping holds."
    elif saw_other:
        status = "cross-readable"; reading = "Agent READ the other tenant's data — mount does NOT isolate; the storage guard is load-bearing."
    else:
        status, reading = "inconclusive", "Could not classify — read the transcript (AccessDenied vs a successful listing)."
    record("03","Cross-tenant reach","Can an agent scoped to user A read user B's prefix or the bucket root?",
           status, reading, {"otherPrefix":bUri, "bucketRoot":rootUri, "transcript":t[:6000]})
''')

# ---------------------------------------------------------------- 11 exp04
md("""## Experiment 04 — Managed-agent hardening controls

(a) Does the API accept `capabilities` / `policies`? Tested via **raw REST** so
the fields actually reach the server (the SDK would strip unknown kwargs).
(b) Is a narrow `network.allowlist` enforced in the sandbox?""")
code('''
findings = {}

# (a) capabilities/policies via REST
caps_id = "probe-controls-caps"
body = {
    "id": caps_id, "base_agent": BASE_AGENT, "description": "isolation-experiment probe",
    "system_instruction": SYS_INSTR,
    "tools": [{"type":"code_execution"},{"type":"filesystem"}],
    "base_environment": {"type":"remote",
        "sources":[{"type":"gcs","source":source_uri(PREFIX_A),"target":"/.agent"}],
        "network":{"allowlist":[{"domain":"*"}]}},
    "capabilities": {"disabled_tools":["RUN_COMMAND"], "enable_subagents": False},
    "policies": [{"tool":"run_command","decision":"DENY"}],
}
sc, j = rest_create_agent(body)
accepted = 200 <= sc < 300
findings["capabilitiesPolicies"] = {"http_status":sc, "accepted":accepted, "response":j}
print("REST create with capabilities/policies -> HTTP", sc, "accepted:", accepted)

if accepted:
    r = run_probe(caps_id, "Run this via shell and print output: echo HARDENING_SHELL_RAN")
    ran = "HARDENING_SHELL_RAN" in r["transcript"]
    findings["runCommandBehaviour"] = ("shell STILL ran despite disabled_tools/deny — control not enforced (or field ignored)"
                                        if ran else "shell did NOT run — RUN_COMMAND appears disabled/denied")
    findings["runCommandTranscript"] = r["transcript"][:1500]

# (b) narrow network allowlist
net_id = "probe-controls-net"
net = make_agent(net_id, prefix=PREFIX_A, network={"allowlist":[{"domain":"example.com"}]})
print("create net agent:", net)
if net.get("ok"):
    INPUT_NET = """Print FULL raw output:
echo '--- non-allowlisted host ---'; curl -s -m 10 -o /dev/null -w '%{http_code}' https://www.google.com 2>&1 || echo CURL_FAILED
echo; echo '--- metadata server ---'; curl -s -m 10 -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' 2>&1 || echo META_BLOCKED"""
    r = run_probe(net_id, INPUT_NET); t = r["transcript"]
    reached_google = bool(re.search(r"\\b200\\b|\\b30\\d\\b", t)) and "CURL_FAILED" not in t
    reached_meta = bool(re.search(r"iam\\.gserviceaccount\\.com", t))
    findings["networkAllowlist"] = {"enforced": (not reached_google and not reached_meta),
        "reachedNonAllowlistedHost":reached_google, "reachedMetadataServer":reached_meta,
        "transcript":t[:1500]}
else:
    findings["networkAllowlist"] = {"error":net.get("error")}

status = "fields-accepted" if accepted else "fields-rejected"
reading = (f"API {'ACCEPTED' if accepted else 'REJECTED'} capabilities/policies (HTTP {sc}). "
           "If rejected, hardening only applies to a self-hosted harness; in managed mode rely on network.allowlist + tool selection.")
record("04","Managed-agent hardening controls",
       "Does the API accept capabilities/policies, and is network.allowlist enforced?",
       status, reading, findings)
''')

# ---------------------------------------------------------------- 12 exp05
md("""## Experiment 05 — MCP reachability

If `GATEWAY_URL` is set, the probe points an `mcp_server` tool at
`<GATEWAY_URL>/mcp` with the bearer header. Otherwise it falls back to the
public **grep** MCP (`https://mcp.grep.app`) just to prove the sandbox can
reach *an* MCP server and list its tools. After running, check the gateway
logs for `POST /mcp/tools/list` or `/mcp/tools/call` with the Authorization
header.""")
code('''
mcp_id = "probe-mcp"
if GATEWAY_URL:
    domain = re.sub(r"^https?://","",GATEWAY_URL).split("/")[0]
    mcp_tool = {"type":"mcp_server","name":"enterprise","url":f"{GATEWAY_URL}/mcp"}
    if MCP_TOKEN: mcp_tool["headers"] = {"Authorization": f"Bearer {MCP_TOKEN}"}
    allow = [{"domain":domain},{"domain":"*.run.app"}]
    target = GATEWAY_URL
else:
    mcp_tool = {"type":"mcp_server","name":"grep-search","url":"https://mcp.grep.app"}
    allow = [{"domain":"mcp.grep.app"}]
    target = "https://mcp.grep.app (public fallback)"
    print("GATEWAY_URL not set — using public grep MCP to prove reachability.")

c = make_agent(mcp_id, prefix=PREFIX_A,
               tools=[{"type":"code_execution"},{"type":"filesystem"},mcp_tool],
               network={"allowlist":allow})
print("create:", c)
if not c.get("ok"):
    record("05","MCP reachability","Can the sandbox reach an /mcp server with the bearer header?",
           "error", f"agents.create with mcp_server failed: {c.get('error')}", {"create":c, "target":target})
else:
    r = run_probe(mcp_id, "List the tools you have available (names only). Then call the MCP tool's tools/list capability if present and report exactly what it returns. If a tool call fails, print the raw error.")
    t = r["transcript"]
    saw = bool(re.search(r"enterprise|grep|search|tools/list|find_enterprise", t, re.I))
    status = "tool-visible" if saw else "inconclusive"
    reading = (("The agent sees the MCP tool. " if saw else "Could not confirm the tool from the transcript. ")
               + "Confirm on the gateway side: logs for POST /mcp/tools/list or /mcp/tools/call with the Authorization header, and note session granularity (stable session vs per-interaction).")
    record("05","MCP reachability","Can the sandbox reach an /mcp server with the bearer header, and what is the session granularity?",
           status, reading, {"target":target, "allowlist":allow, "transcript":t[:4000]})
''')

# ---------------------------------------------------------------- 13 report
md("## Report — assemble + save\nRenders a markdown report, prints it, and (if the repo is present) writes it to `experiments/findings/`.")
code('''
order = ["01","02","03","04","05"]
ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
L = []
L.append("# Sandbox isolation experiment report\\n")
L.append(f"- project: `{PROJECT_ID}` · location: `{LOCATION}` · base_agent: `{BASE_AGENT}`")
L.append(f"- bucket: `gs://{GCS_BUCKET}` · prefixes: `{PREFIX_A}` / `{PREFIX_B}`")
L.append(f"- gateway: `{GATEWAY_URL or '(not set)'}` · generated: {ts}")
L.append("- runner: Managed Agents Python SDK notebook (streaming interactions)\\n")

# operator summary
L.append("## Operator summary\\n")
def st(rid): return RESULTS.get(rid,{}).get("status","(not run)")
L.append(f"- **01 identity** — `{st('01')}`: {RESULTS.get('01',{}).get('interpretation','')[:160]}")
L.append(f"- **02 two-agents** — `{st('02')}`: {RESULTS.get('02',{}).get('interpretation','')[:160]}")
L.append(f"- **03 cross-tenant** — `{st('03')}`: {RESULTS.get('03',{}).get('interpretation','')[:160]}")
L.append(f"- **04 controls** — `{st('04')}`: {RESULTS.get('04',{}).get('interpretation','')[:160]}")
L.append(f"- **05 mcp** — `{st('05')}`: {RESULTS.get('05',{}).get('interpretation','')[:160]}")
L.append(f"- project `{PROJECT_ID}`, base_agent `{BASE_AGENT}`. Interactions require `background=true` + `Api-Revision: 2026-05-20`; agent creation is async (poll/retry through `404`/`setup in progress`).\\n")

L.append("## Summary\\n")
L.append("| # | Experiment | Status | One-line reading |")
L.append("| --- | --- | --- | --- |")
for rid in order:
    r = RESULTS.get(rid)
    if r: L.append(f"| {rid} | {r['title']} | **{r['status']}** | {r['interpretation'].splitlines()[0][:120]} |")
    else: L.append(f"| {rid} | (not run) | **n/a** | — |")
L.append("")
for rid in order:
    r = RESULTS.get(rid)
    if not r: continue
    L.append(f"## {rid} — {r['title']}\\n")
    L.append(f"**Question:** {r['question']}\\n")
    L.append(f"**Status:** `{r['status']}`\\n")
    L.append(f"**Reading:** {r['interpretation']}\\n")
    L.append("```")
    L.append(json.dumps(r['evidence'], indent=2, default=str)[:6000])
    L.append("```\\n")
report = "\\n".join(L)
print(report)

# save next to the repo if we can find experiments/findings
for base in ("experiments/findings", "../findings", "findings"):
    try:
        os.makedirs(base, exist_ok=True)
        with open(f"{base}/REPORT-{ts}.md","w") as f: f.write(report)
        with open(f"{base}/LATEST.md","w") as f: f.write(report)
        print(f"\\nsaved -> {base}/LATEST.md and {base}/REPORT-{ts}.md")
        break
    except Exception as e:
        continue
''')

# ---------------------------------------------------------------- 14 cleanup
md("## Cleanup — delete all probe agents")
code('''
for aid in PROBE_IDS:
    for _ in range(6):
        try:
            client.agents.delete(id=aid); print("deleted", aid); break
        except Exception as e:
            m = str(e)
            if "being created" in m.lower():
                time.sleep(10); continue
            print("skip", aid, ":", m[:80]); break
try:
    resp = client.agents.list()
    print("remaining:", [a.id for a in resp.agents] if getattr(resp,"agents",None) else "None")
except Exception as e:
    print("list err:", str(e)[:80])
''')

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4, "nbformat_minor": 5,
}

out = os.path.join(os.path.dirname(__file__), "isolation_experiments.ipynb")
with open(out, "w") as f:
    json.dump(nb, f, indent=1)
print("wrote", out, "with", len(cells), "cells")
