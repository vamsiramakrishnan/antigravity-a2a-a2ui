"""HTTP MCP-style endpoint that re-exposes the enterprise proxy tools.

The wrapped runtime is a Google Managed Agent, so instead of the session-file
tool hack we register the enterprise proxy as an ``McpStreamableHttpServer`` the
sandbox agent calls. This router is that server: a minimal JSON-RPC-ish surface
with two POST endpoints —

* ``/mcp/tools/list``  -> the tool catalog (name, description, input schema)
* ``/mcp/tools/call``  -> dispatch ``{name, arguments}`` to the enterprise logic

The catalog mirrors the callables in :mod:`a2a_workspace.gemini_enterprise.tools`
(``search_enterprise``, ``list_enterprise_agents``, ``invoke_enterprise_agent``,
``apply_enterprise_skill``, ``find_enterprise_skills``). Each call dispatches to
the **same** underlying enterprise logic path as
:mod:`a2a_workspace.gateway.enterprise_api` — we do not duplicate the Discovery
Engine call. Rather than reach into the container directly, the router depends on
an injected :class:`EnterpriseService` (the small operation surface) and an
injected ``verify_session`` dependency, so it is testable and importable without
any google SDK.

Auth is identical to the enterprise endpoints: the caller must present the
session proxy token as ``Authorization: Bearer ...``; anything else is rejected.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from a2a_workspace.identity.session_token import SessionToken


class EnterpriseService(Protocol):
    """The enterprise operations the MCP tools dispatch to.

    Each method takes the verified :class:`SessionToken` and returns plain text /
    structured results — never credentials. The composition root implements this
    by routing through the same per-request DiscoveryEngine client that
    :mod:`gateway.enterprise_api` builds (it holds the user's token server-side).
    """

    def search(self, st: SessionToken, *, query: str, google_search: bool = False) -> str: ...

    def list_agents(self, st: SessionToken) -> list[dict]: ...

    def invoke_agent(self, st: SessionToken, *, agent_id: str, query: str) -> str: ...

    def apply_skill(self, st: SessionToken, *, skill_name: str, text: str) -> str: ...

    def find_skills(self, st: SessionToken, *, query: str) -> list[dict]: ...


# A dependency that verifies the inbound bearer token into a SessionToken or
# raises HTTPException(401/403). Injected so the router does not hardcode the
# container (mirrors how enterprise_api accepts a verify dependency).
VerifySession = "Callable[..., SessionToken]"


class _CallBody(BaseModel):
    name: str
    arguments: dict = {}


# -- tool catalog ----------------------------------------------------------
# Mirrors gemini_enterprise/tools.py. Kept declarative so /tools/list is a pure
# read and the schemas stay next to the dispatch table.

_TOOL_CATALOG: list[dict] = [
    {
        "name": "search_enterprise",
        "description": (
            "Search the user's connected enterprise data (SharePoint, Jira, "
            "GitHub, Salesforce, and other Gemini Enterprise connectors) and "
            "return a grounded answer with citations."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "list_enterprise_agents",
        "description": (
            "List the other Gemini Enterprise agents that can be invoked, with "
            "their ids and descriptions."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "invoke_enterprise_agent",
        "description": (
            "Delegate a query to another registered Gemini Enterprise agent and "
            "return its answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["agent_id", "query"],
        },
    },
    {
        "name": "apply_enterprise_skill",
        "description": (
            "Apply a named Gemini Enterprise skill (e.g. \"Brand Voice\", "
            "\"Contract Review\") to the given text and return the result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["skill_name", "text"],
        },
    },
    {
        "name": "find_enterprise_skills",
        "description": (
            "Discover relevant skills in the Skill Registry by intent, then use "
            "apply_enterprise_skill."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def _format_agents(agents: list[dict]) -> str:
    if not agents:
        return "No registered agents are available."
    return "\n".join(
        f"- {a['agent_id']}: {a.get('display_name') or a.get('description', '')}"
        for a in agents
    )


def _format_skills(skills: list[dict]) -> str:
    if not skills:
        return "No matching skills found."
    return "\n".join(
        f"- {s.get('display_name') or s.get('skill_id')}: {s.get('description', '')}"
        for s in skills
    )


def create_mcp_router(
    *,
    verify_session,
    enterprise: EnterpriseService,
) -> APIRouter:
    """Build the MCP router bound to a verify dependency and enterprise service.

    Both are injected so this is unit-testable with fakes and never imports a
    google SDK. ``verify_session`` is a FastAPI dependency callable returning a
    :class:`SessionToken` (or raising 401/403). ``enterprise`` performs the
    actual proxy calls.
    """
    router = APIRouter(prefix="/mcp", tags=["mcp"])

    @router.post("/tools/list")
    def tools_list(st: SessionToken = Depends(verify_session)) -> dict:
        # Listing requires a valid session token too: an unauthenticated caller
        # should learn nothing about the available surface.
        return {"tools": _TOOL_CATALOG}

    @router.post("/tools/call")
    def tools_call(
        body: _CallBody,
        st: SessionToken = Depends(verify_session),
    ) -> dict:
        name = body.name
        args = body.arguments or {}
        if name not in _DISPATCH:
            raise HTTPException(status_code=404, detail=f"unknown tool: {name}")
        try:
            text = _dispatch(enterprise, st, name, args)
        except _MissingArgument as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"content": [{"type": "text", "text": text}]}

    return router


class _MissingArgument(Exception):
    """A required tool argument was absent -> maps to HTTP 400."""


def _arg(args: dict, key: str) -> str:
    try:
        return args[key]
    except KeyError as exc:
        raise _MissingArgument(f"missing required argument: {key}") from exc


# Names that have a dispatch entry. Membership check lets /tools/call return 404
# for an unknown tool while still distinguishing a missing-argument 400.
_DISPATCH = frozenset(t["name"] for t in _TOOL_CATALOG)


def _dispatch(
    enterprise: EnterpriseService, st: SessionToken, name: str, args: dict
) -> str:
    if name == "search_enterprise":
        return enterprise.search(st, query=_arg(args, "query"))
    if name == "list_enterprise_agents":
        return _format_agents(enterprise.list_agents(st))
    if name == "invoke_enterprise_agent":
        return enterprise.invoke_agent(
            st, agent_id=_arg(args, "agent_id"), query=_arg(args, "query")
        )
    if name == "apply_enterprise_skill":
        return enterprise.apply_skill(
            st, skill_name=_arg(args, "skill_name"), text=_arg(args, "text")
        )
    if name == "find_enterprise_skills":
        return _format_skills(enterprise.find_skills(st, query=_arg(args, "query")))
    raise KeyError(name)  # pragma: no cover - guarded by _DISPATCH membership
