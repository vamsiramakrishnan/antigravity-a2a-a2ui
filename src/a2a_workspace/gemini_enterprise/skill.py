"""Generate a publishable Antigravity skill bundle for Gemini Enterprise.

The bundle is a ``{relpath: bytes}`` map (the same shape a
:class:`~a2a_workspace.registry.drafts.Draft` holds), so it can be published as
an immutable workspace revision through the bounded draft pipeline *or* written
to a ``skills_paths`` directory. Making it a breeze to set up is the whole point:
one call yields a ready-to-publish capability.

Contents:

* ``SKILL.md`` — on-demand guidance telling the agent when/how to reach the
  user's connectors and when to delegate to another registered agent.
* ``manifest.json`` — the revision manifest.
* ``tools/enterprise_tools.py`` — a generated module whose ``get_tools()`` builds
  the proxy callables from the session file the materializer drops in.
"""

from __future__ import annotations

import json

SKILL_MD = """\
---
name: gemini-enterprise-connectors
description: >-
  Answer questions from the user's connected enterprise data (SharePoint, Jira,
  GitHub, Salesforce, and other Gemini Enterprise connectors) and delegate to
  other registered Gemini Enterprise agents when appropriate.
version: 1
---

# Gemini Enterprise Connectors

You can reach the user's connected enterprise data and other registered agents
through these tools. They run as proxies through the trusted control plane, so
you never see or handle any OAuth credential.

## When to use which tool

- **search_enterprise(query)** — the user asks about something that likely lives
  in their connected systems (tickets, docs, repos, CRM records). Prefer this
  over guessing; it returns a grounded answer with citations.
- **answer_with_web(query)** — the question benefits from public/web context in
  addition to enterprise data.
- **list_enterprise_agents()** — you are unsure whether a specialized agent owns
  this task. Call it first to see available agents.
- **invoke_enterprise_agent(agent_id, query)** — a different registered agent is
  better suited (e.g. a domain-specific assistant). Delegate and relay its
  answer.

## Rules

- Always cite sources returned by the tools.
- Do not attempt to authenticate or ask the user for tokens; authorization is
  handled by Gemini Enterprise and the gateway.
- If a tool reports no connected data or no agents, say so plainly rather than
  fabricating an answer.
"""

ENTERPRISE_TOOLS_PY = '''\
"""Generated: builds the Gemini Enterprise proxy tools for this session.

The Antigravity runtime imports this and calls ``get_tools()`` to obtain the
callables to register. Credentials are never embedded here; the session proxy
token is read at call time from the session file the control plane provided.
"""

import json
import os
from pathlib import Path

from a2a_workspace.gemini_enterprise.tools import make_enterprise_tools


def _session_file() -> Path:
    # The materializer writes this into app_data_dir; the agent process reads it.
    app_data = os.environ.get("A2A_APP_DATA_DIR", ".")
    return Path(app_data) / ".a2a" / "session.json"


def get_tools():
    info = json.loads(_session_file().read_text())
    gateway_url = info["gateway_url"]

    def token_provider() -> str:
        # Re-read each call so a refreshed token is picked up transparently.
        return json.loads(_session_file().read_text())["session_token"]

    return make_enterprise_tools(
        gateway_url=gateway_url, token_provider=token_provider
    )
'''


def generate_skill_bundle() -> dict[str, bytes]:
    """Return the enterprise skill bundle as a publishable file map."""
    manifest = {
        "name": "gemini-enterprise-connectors",
        "version": 1,
        "description": (
            "Connectors and agent-to-agent access for Gemini Enterprise, exposed "
            "as proxy tools that never hold credentials."
        ),
        "entrypoints": {"tools": "tools/enterprise_tools.py:get_tools"},
    }
    return {
        "manifest.json": json.dumps(manifest, indent=2, sort_keys=True).encode(),
        "SKILL.md": SKILL_MD.encode(),
        "tools/enterprise_tools.py": ENTERPRISE_TOOLS_PY.encode(),
    }


def publish_enterprise_skill(drafts, workspace_id: str, *, storage, activate=True):
    """Convenience: push the bundle through the bounded draft pipeline.

    ``drafts`` is a :class:`~a2a_workspace.registry.drafts.DraftService`. Returns
    the :class:`~a2a_workspace.registry.drafts.PublishResult`.
    """
    bundle = generate_skill_bundle()
    draft = drafts.create_draft(workspace_id)
    for path, content in bundle.items():
        drafts.apply_patch(draft.draft_id, path=path, content=content)
    drafts.validate(draft.draft_id)
    drafts.submit(draft.draft_id)
    return drafts.publish(
        draft.draft_id, storage=storage, activate=activate, note="gemini-enterprise"
    )
