"""Enterprise-proxy endpoints: the credentialed boundary the agent calls back to.

The Antigravity runtime reaches Gemini Enterprise *only* through these endpoints,
authenticated with its short-lived **session proxy token** — never the user's
OAuth credential. Each endpoint:

1. verifies the session token -> ``(principal_key, conversation_id)``,
2. looks up the user's Discovery Engine token held server-side for that
   conversation (the decrypt boundary; the runtime never sees it),
3. builds a per-request :class:`DiscoveryEngineClient` under that token, and
4. returns plain results (text + citations), no credentials.

This is what lets the universal agent *use* connectors and other agents while
holding none of the keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from a2a_workspace.container import Container
from a2a_workspace.errors import AuthorizationError
from a2a_workspace.gateway.dependencies import get_container
from a2a_workspace.identity.session_token import SessionToken

router = APIRouter(prefix="/enterprise", tags=["enterprise"])


class AssistBody(BaseModel):
    query: str
    google_search: bool = False
    data_store_ids: list[str] = []


class InvokeAgentBody(BaseModel):
    agent_id: str
    query: str


class ApplySkillBody(BaseModel):
    skill_name: str
    text: str


class FindSkillsBody(BaseModel):
    query: str


def session_token(
    request: Request,
    authorization: str = Header(default=""),
) -> SessionToken:
    container: Container = request.app.state.container
    try:
        return container.session_tokens.verify(authorization)
    except AuthorizationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _client_for(container: Container, st: SessionToken):
    if not container.config.gemini.is_configured():
        raise HTTPException(
            status_code=503, detail="Gemini Enterprise integration is not configured"
        )
    ge_token = container.session_credentials.get(st.conversation_id)
    if not ge_token:
        # No stored user credential for this session: the agent cannot act on the
        # user's behalf. Fail closed rather than reaching for an ambient identity.
        raise HTTPException(
            status_code=403,
            detail="no user credential is associated with this session",
        )
    return container.discovery_client_factory(ge_token)


def _ge_session(container: Container, st: SessionToken) -> str | None:
    # Reuse the inbound streamAssist session so connector calls share its history
    # and uploaded context files. Empty -> the client allocates a new session.
    return container.session_credentials.get_session(st.conversation_id) or None


@router.post("/assist")
def assist(
    body: AssistBody,
    st: SessionToken = Depends(session_token),
    container: Container = Depends(get_container),
) -> dict:
    client = _client_for(container, st)
    result = client.assist(
        body.query,
        session=_ge_session(container, st),
        data_store_ids=tuple(body.data_store_ids),
        google_search=body.google_search,
    )
    return {
        "text": result.as_text(),
        "citations": [
            {"title": c.title, "uri": c.uri, "snippet": c.snippet}
            for c in result.citations
        ],
    }


@router.post("/agents/list")
def list_agents(
    st: SessionToken = Depends(session_token),
    container: Container = Depends(get_container),
) -> dict:
    client = _client_for(container, st)
    return {
        "agents": [
            {
                "agent_id": a.agent_id,
                "display_name": a.display_name,
                "description": a.description,
            }
            for a in client.list_agents()
        ]
    }


@router.post("/agents/invoke")
def invoke_agent(
    body: InvokeAgentBody,
    st: SessionToken = Depends(session_token),
    container: Container = Depends(get_container),
) -> dict:
    client = _client_for(container, st)
    result = client.invoke_agent(
        body.agent_id, body.query, session=_ge_session(container, st)
    )
    return {"text": result.as_text()}


@router.post("/skill")
def apply_skill(
    body: ApplySkillBody,
    st: SessionToken = Depends(session_token),
    container: Container = Depends(get_container),
) -> dict:
    """Apply a Gemini Enterprise assistant skill (Brand Voice, Contract Review, …).

    Routed assistant-mode (no agentsSpec) so the assistant's Skills apply.
    """
    client = _client_for(container, st)
    result = client.apply_skill(
        body.skill_name, body.text, session=_ge_session(container, st)
    )
    return {"text": result.as_text()}


@router.post("/skills/find")
def find_skills(
    body: FindSkillsBody,
    st: SessionToken = Depends(session_token),
    container: Container = Depends(get_container),
) -> dict:
    """Semantic discovery over the Skill Registry, so the agent finds skills."""
    ge_token = container.session_credentials.get(st.conversation_id)
    if not ge_token:
        raise HTTPException(
            status_code=403, detail="no user credential is associated with this session"
        )
    client = container.skill_registry_client_factory(ge_token)
    return {
        "skills": [
            {"skill_id": s.skill_id, "display_name": s.display_name, "description": s.description}
            for s in client.retrieve_skills(body.query)
        ]
    }
