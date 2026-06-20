"""Serve the A2A agent card at the path ``agents-cli`` / Discovery Engine expect.

``agents-cli publish gemini-enterprise --registration-type a2a`` fetches the
agent card from a well-known URL and registers it. The canonical path it (and
Discovery Engine) look for is::

    {base_url}/a2a/app/.well-known/agent-card.json

Our gateway already serves a richer, source-of-truth card at
``/.well-known/agent.json`` (:func:`a2a_workspace.gateway.a2a.agent_card`). This
router does **not** duplicate that content: it accepts the existing card-provider
callable and adapts its output through
:func:`a2a_workspace.integrations.agents_cli.agent_card_to_a2a`, so there is one
source of card truth.

Importable without any Google SDKs — it depends only on FastAPI and the pure
integrations helper.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from a2a_workspace.integrations.agents_cli import agent_card_to_a2a

# A callable that returns the gateway's native agent card dict (the same shape
# a2a_workspace.gateway.a2a.agent_card produces).
CardProvider = Callable[[], dict]


def create_agents_cli_compat_router(
    card_provider: CardProvider, base_url: str
) -> APIRouter:
    """Build a router serving the adapted A2A card at the well-known paths.

    The card is served at both the agents-cli canonical path
    (``/a2a/app/.well-known/agent-card.json``) and the bare A2A well-known path
    (``/.well-known/agent-card.json``) so either discovery convention resolves.
    Both adapt the same provider output, so card content is never duplicated.
    """
    router = APIRouter()

    def _card() -> dict:
        return agent_card_to_a2a(card_provider(), base_url=base_url)

    @router.get("/a2a/app/.well-known/agent-card.json")
    def agents_cli_agent_card() -> dict:
        return _card()

    @router.get("/.well-known/agent-card.json")
    def well_known_agent_card() -> dict:
        return _card()

    return router
