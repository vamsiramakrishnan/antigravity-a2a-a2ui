"""Antigravity tools that expose Gemini Enterprise — as proxies, not credentials.

The Antigravity SDK registers plain Python callables as tools
(``LocalAgentConfig(tools=[fn, ...])``); it derives each tool's schema from the
function signature and docstring. These callables run *inside the agent runtime*,
so by design they hold **no** OAuth credential. Instead each one makes an
authenticated call back to the control-plane endpoints
(:mod:`a2a_workspace.gateway.enterprise_api`), which holds the user's delegated
token and performs the actual Discovery Engine call.

The only secret the runtime sees is a short-lived, session-scoped *proxy token*
that authorizes exactly these callbacks for this principal and nothing else — it
cannot read storage, cannot be refreshed, and expires quickly. The durable user
OAuth/refresh credential never leaves the gateway. That is the whole point of the
proxy shape.

Use :func:`make_enterprise_tools` to get a ready-to-register list of tools.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from a2a_workspace.gemini_enterprise.transport import Transport, UrllibTransport

# A function that returns the current session-scoped proxy token (e.g. reads the
# file the materializer dropped into app_data_dir). Kept as a provider, not a
# value, so a token refresh within the session is transparent to the tools.
TokenProvider = Callable[[], str]


def make_enterprise_tools(
    *,
    gateway_url: str,
    token_provider: TokenProvider,
    transport: Transport | None = None,
) -> list[Callable]:
    """Build the list of Antigravity tool callables bound to one gateway/session.

    Pass the result straight to ``LocalAgentConfig(tools=...)``.
    """
    http = transport or UrllibTransport()
    base = gateway_url.rstrip("/")

    def _call(path: str, payload: dict) -> dict:
        resp = http.request(
            "POST",
            f"{base}{path}",
            headers={
                "Authorization": f"Bearer {token_provider()}",
                "Content-Type": "application/json",
            },
            body=json.dumps(payload).encode(),
        )
        return resp.json() or {}

    def search_enterprise(query: str) -> str:
        """Search the user's connected enterprise data (SharePoint, Jira, GitHub,
        Salesforce, and other Gemini Enterprise connectors) and return a grounded
        answer with citations.

        Args:
            query: The natural-language question to answer from connected data.
        """
        result = _call("/enterprise/assist", {"query": query})
        return result.get("text", "(no answer)")

    def answer_with_web(query: str) -> str:
        """Answer a question grounded in both the user's connected enterprise data
        and Google Search. Use when public/web context may help.

        Args:
            query: The natural-language question to answer.
        """
        result = _call("/enterprise/assist", {"query": query, "google_search": True})
        return result.get("text", "(no answer)")

    def list_enterprise_agents() -> str:
        """List the other Gemini Enterprise agents that can be invoked, with their
        ids and descriptions. Call this before invoke_enterprise_agent if unsure
        which agent to use.
        """
        result = _call("/enterprise/agents/list", {})
        agents = result.get("agents", [])
        if not agents:
            return "No registered agents are available."
        return "\n".join(
            f"- {a['agent_id']}: {a.get('display_name') or a.get('description', '')}"
            for a in agents
        )

    def invoke_enterprise_agent(agent_id: str, query: str) -> str:
        """Delegate a query to another registered Gemini Enterprise agent and
        return its answer. Use for specialized tasks owned by a different agent.

        Args:
            agent_id: The id of the agent to invoke (see list_enterprise_agents).
            query: The natural-language request to delegate.
        """
        result = _call(
            "/enterprise/agents/invoke", {"agent_id": agent_id, "query": query}
        )
        return result.get("text", "(no answer)")

    return [
        search_enterprise,
        answer_with_web,
        list_enterprise_agents,
        invoke_enterprise_agent,
    ]
