"""ADK agent for agents-cli — a per-user Antigravity skill assistant.

``agents-cli`` / ADK load this module and expect two module-level objects:

* ``root_agent`` — a ``google.adk.agents.Agent``.
* ``app`` — a ``google.adk.apps.App`` wrapping it.

Both are built from :data:`ROOT_AGENT_SPEC`, a plain dict describing the agent
(name, model, description, instruction, tool names). The spec is importable and
testable **without** ``google-adk`` installed; the real ADK construction is
import-guarded behind ``try/except ImportError`` so this module imports cleanly
in environments (like this repo's venv) that have no Google SDKs.

The agent reuses the *same* enterprise proxy tools the gateway brokers
(:func:`a2a_workspace.gemini_enterprise.tools.make_enterprise_tools`). Those are
plain Python callables — ADK derives each tool's schema from the signature and
docstring and accepts them directly, exactly as the Antigravity SDK does. The
tools hold no credential; they call back to the gateway with a short-lived
session-scoped proxy token resolved from the environment / session file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from a2a_workspace.gemini_enterprise.tools import make_enterprise_tools

# Where the gateway's proxy tools call back to, and where the session token
# lives. On Cloud Run / Agent Runtime these come from the deployment env; the
# token may instead be in the session file the gateway materializes.
_DEFAULT_GATEWAY_URL = "http://localhost:8080"
_SESSION_FILE_ENV = "A2A_SESSION_FILE"
_DEFAULT_SESSION_FILE = ".a2a/session.json"


# Plain, ADK-free description of the agent. Tests assert on this directly.
ROOT_AGENT_SPEC: dict = {
    "name": "antigravity_skill_assistant",
    "model": "gemini-2.5-flash",
    "description": (
        "Per-user Antigravity skill assistant that answers from the user's "
        "Gemini Enterprise connectors and applies or discovers enterprise skills."
    ),
    "instruction": (
        "You are a per-user Antigravity skill assistant operating inside a "
        "Gemini Enterprise app. Answer the user's questions by grounding in their "
        "connected enterprise data using `search_enterprise` (add web context "
        "with `answer_with_web` only when public information helps), always "
        "preferring cited, grounded answers.\n\n"
        "When a task calls for a named capability, discover it with "
        "`find_enterprise_skills` and apply it with `apply_enterprise_skill`. To "
        "delegate specialized work, use `list_enterprise_agents` then "
        "`invoke_enterprise_agent`.\n\n"
        "You never see raw credentials: every tool proxies through the control "
        "plane. Be concise, cite sources when available, and say when you cannot "
        "find an answer rather than guessing."
    ),
    # Tool names match the callables returned by make_enterprise_tools.
    "tools": [
        "search_enterprise",
        "answer_with_web",
        "list_enterprise_agents",
        "invoke_enterprise_agent",
        "apply_enterprise_skill",
        "find_enterprise_skills",
    ],
}


def _session_token_provider() -> Callable[[], str]:
    """Return a provider that resolves the session-scoped proxy token at call time.

    Resolution order, re-checked on every call so a token refresh is transparent:

    1. ``A2A_SESSION_TOKEN`` environment variable, if set.
    2. The ``session_token`` field of the session file the gateway materialized
       (path from ``A2A_SESSION_FILE``, default ``.a2a/session.json`` under the
       agent's app-data dir / cwd).
    """

    def provider() -> str:
        token = os.environ.get("A2A_SESSION_TOKEN")
        if token:
            return token
        session_file = Path(
            os.environ.get(_SESSION_FILE_ENV, _DEFAULT_SESSION_FILE)
        )
        if session_file.is_file():
            try:
                data = json.loads(session_file.read_text())
            except (OSError, ValueError):
                return ""
            return str(data.get("session_token", ""))
        return ""

    return provider


def build_tools(
    gateway_url: str | None = None,
    token_provider: Callable[[], str] | None = None,
) -> list[Callable]:
    """Build the enterprise tool callables for the ADK agent.

    ``gateway_url`` defaults to ``A2A_GATEWAY_URL`` (then a localhost fallback);
    ``token_provider`` defaults to :func:`_session_token_provider`. The returned
    callables are the same ones the Antigravity runtime uses, so the ADK
    reasoning engine and our managed agents share one tool surface.
    """
    url = gateway_url or os.environ.get("A2A_GATEWAY_URL", _DEFAULT_GATEWAY_URL)
    provider = token_provider or _session_token_provider()
    return make_enterprise_tools(gateway_url=url, token_provider=provider)


def _build_adk_objects():
    """Construct the real ADK ``root_agent`` and ``app``.

    Import-guarded: returns ``(None, None)`` if ``google-adk`` is not installed,
    so importing this module never requires the SDK. agents-cli / a deployed
    reasoning engine will have the SDK and get real objects.
    """
    try:
        from google.adk.agents import Agent  # type: ignore
        from google.adk.apps import App  # type: ignore
        from google.adk.models import Gemini  # type: ignore
    except ImportError:  # pragma: no cover - SDK absent in this venv
        return None, None

    agent = Agent(
        name=ROOT_AGENT_SPEC["name"],
        model=Gemini(model=ROOT_AGENT_SPEC["model"]),
        description=ROOT_AGENT_SPEC["description"],
        instruction=ROOT_AGENT_SPEC["instruction"],
        tools=build_tools(),
    )
    application = App(root_agent=agent, name=ROOT_AGENT_SPEC["name"])
    return agent, application


# Module-level objects agents-cli / ADK look for. ``None`` when the SDK is absent
# (e.g. this repo's test venv); real objects when google-adk is installed.
root_agent, app = _build_adk_objects()
