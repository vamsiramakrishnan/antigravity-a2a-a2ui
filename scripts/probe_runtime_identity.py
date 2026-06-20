#!/usr/bin/env python3
"""probe_runtime_identity.py — REST fallback for the sandbox-identity probe.

WHY THIS EXISTS
---------------
Same job as scripts/probe_sandbox_identity.mjs: settle ADR 0001's one open
question — what identity does the Managed Agents sandbox harness run as? We do
that by making the sandbox introspect its OWN ambient credentials and stream the
result back (whoami / id / gcloud token scopes / ADC / metadata SA email).

This is the BEST-EFFORT fallback for operators without Node. The canonical probe
is the @google/genai script (probe_sandbox_identity.mjs): it tracks the SDK's
exact request/response shape, this REST shim only approximates the
aiplatform v1beta1 interactions surface and may need the path/body nudged for
your API version. Prefer the Node path when you can.

Dependency-light by design: standard library only (urllib + json + subprocess).
The bearer token comes from `gcloud auth print-access-token` (the operator's own
identity — NOT the sandbox's; the sandbox identity is what we are MEASURING via
the probe output).

HOW TO READ THE RESULT — three outcomes (identical to the Node header):
  1. "NO AMBIENT TOKEN" / no ADC / metadata "none" -> no GCP identity in the
     runtime; isolation enforced upstream (best).
  2. scoped/downscoped token (narrow scope, short expiry, per-agent principal)
     -> per-agent IAM is the enforced boundary (good).
  3. broad shared SA email + cloud-platform scope -> shared service account;
     the storage-adapter guard + ADR 2 in-runtime policy is the boundary (worst).

USAGE:
  PROJECT_ID=my-proj AGENT=my-agent-id ./.venv/bin/python scripts/probe_runtime_identity.py

ENV VARS:
  PROJECT_ID  (required)  GCP project id/number.
  LOCATION    (optional)  Vertex location. Default "global".
  AGENT       (required)  Existing managed agent id whose sandbox we probe.
  API_HOST    (optional)  Override the aiplatform host. Default derives from
                          LOCATION ("global" -> aiplatform.googleapis.com,
                          else "<loc>-aiplatform.googleapis.com").
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# Same single-line introspection command as the Node probe. Tries every place an
# ambient credential could hide so we can classify the runtime identity.
PROBE_INPUT = (
    "Run this and show the full output: "
    "echo '--- whoami ---'; whoami; id; "
    "echo '--- gcloud token scopes ---'; "
    "TOK=$(gcloud auth print-access-token 2>/dev/null); "
    'if [ -n "$TOK" ]; then '
    'curl -s "https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=$TOK"; '
    "else echo 'NO AMBIENT TOKEN'; fi; "
    "echo '--- adc ---'; ls -la ~/.config/gcloud 2>/dev/null; "
    "echo '--- metadata SA ---'; "
    "curl -s -H 'Metadata-Flavor: Google' "
    "'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email' "
    "2>/dev/null || echo none"
)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.stderr.write(
            f"ERROR: missing required env var {name}.\n"
            "  required: PROJECT_ID, AGENT. optional: LOCATION, API_HOST.\n"
        )
        sys.exit(2)
    return value


def access_token() -> str:
    """Operator's bearer via gcloud. This is NOT the sandbox identity."""
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        sys.stderr.write("ERROR: gcloud not found on PATH.\n")
        sys.exit(3)
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"ERROR: gcloud auth print-access-token failed: {exc.stderr}\n")
        sys.exit(3)
    return out.stdout.strip()


def main() -> None:
    project = require_env("PROJECT_ID")
    agent = require_env("AGENT")
    location = os.environ.get("LOCATION", "global")
    host = os.environ.get("API_HOST") or (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )

    token = access_token()

    # Best-effort path for the Interactions data plane. Adjust to your API
    # version if it 404s — the Node SDK is authoritative for the exact shape.
    agent_resource = f"projects/{project}/locations/{location}/agents/{agent}"
    url = f"https://{host}/v1beta1/{agent_resource}:interact"

    # store/stream omitted from the REST body for the simplest synchronous call;
    # add "stream": True and switch to :streamInteract for your version if needed.
    body = {
        "agent": agent_resource,
        "input": PROBE_INPUT,
        "environment": {"type": "remote"},
        "store": True,
    }

    sys.stderr.write(
        f"[probe] (REST best-effort) POST {url}\n"
        f"[probe] project={project} location={location} agent={agent}\n"
    )

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-goog-user-project": project,
        },
    )

    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read().decode("utf-8")
            # Print raw — scan for the markers (--- whoami ---, NO AMBIENT TOKEN,
            # the SA email) to classify against the three outcomes above.
            print(payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        sys.stderr.write(
            f"[probe] HTTP {exc.code} {exc.reason}\n{detail}\n"
            "[probe] If this is a 404/400 on the path or body, the REST shape "
            "differs for your API version — use the canonical Node probe "
            "(scripts/probe_sandbox_identity.mjs).\n"
        )
        sys.exit(1)
    except urllib.error.URLError as exc:
        sys.stderr.write(f"[probe] network error: {exc.reason}\n")
        sys.exit(1)

    sys.stderr.write(
        "\n[probe] Interpret against the three outcomes in this file's docstring:\n"
        "  NO AMBIENT TOKEN -> isolation enforced upstream (best)\n"
        "  scoped/downscoped token -> per-agent IAM is the boundary (good)\n"
        "  broad shared SA email -> storage-guard + in-runtime policy is the boundary (worst)\n"
    )


if __name__ == "__main__":
    main()
