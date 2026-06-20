from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from a2a_workspace.gateway.mcp_server import create_mcp_router
from a2a_workspace.identity.session_token import SessionToken


class FakeEnterprise:
    """Records calls; returns canned text/structured results."""

    def __init__(self):
        self.calls: list[tuple] = []

    def search(self, st, *, query, google_search=False):
        self.calls.append(("search", query))
        return f"answer to: {query}"

    def list_agents(self, st):
        self.calls.append(("list_agents",))
        return [{"agent_id": "a1", "display_name": "Agent One", "description": "d"}]

    def invoke_agent(self, st, *, agent_id, query):
        self.calls.append(("invoke_agent", agent_id, query))
        return f"{agent_id} says hi"

    def apply_skill(self, st, *, skill_name, text):
        self.calls.append(("apply_skill", skill_name, text))
        return f"{skill_name}: {text}"

    def find_skills(self, st, *, query):
        self.calls.append(("find_skills", query))
        return [{"skill_id": "s1", "display_name": "Brand Voice", "description": "bv"}]


def _verify_session(authorization: str = Header(default="")) -> SessionToken:
    """Fake verify: accepts 'Bearer good', rejects everything else."""
    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if token != "good":
        raise HTTPException(status_code=401, detail="invalid session token")
    return SessionToken(principal_key="prn_x", conversation_id="cid", expires_at_epoch=9e9)


def _client():
    enterprise = FakeEnterprise()
    app = FastAPI()
    app.include_router(
        create_mcp_router(verify_session=_verify_session, enterprise=enterprise)
    )
    return TestClient(app), enterprise


_OK = {"Authorization": "Bearer good"}


def test_tools_list_returns_catalog():
    client, _ = _client()
    resp = client.post("/mcp/tools/list", headers=_OK)
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tools"]}
    assert names == {
        "search_enterprise",
        "list_enterprise_agents",
        "invoke_enterprise_agent",
        "apply_enterprise_skill",
        "find_enterprise_skills",
    }
    # schemas present.
    search = next(t for t in resp.json()["tools"] if t["name"] == "search_enterprise")
    assert search["inputSchema"]["required"] == ["query"]


def test_tools_call_search_dispatches():
    client, enterprise = _client()
    resp = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={"name": "search_enterprise", "arguments": {"query": "revenue?"}},
    )
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "answer to: revenue?"
    assert enterprise.calls == [("search", "revenue?")]


def test_tools_call_list_agents_formats_text():
    client, _ = _client()
    resp = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={"name": "list_enterprise_agents", "arguments": {}},
    )
    assert resp.status_code == 200
    assert "a1: Agent One" in resp.json()["content"][0]["text"]


def test_tools_call_invoke_agent():
    client, enterprise = _client()
    resp = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={
            "name": "invoke_enterprise_agent",
            "arguments": {"agent_id": "a1", "query": "do it"},
        },
    )
    assert resp.json()["content"][0]["text"] == "a1 says hi"
    assert ("invoke_agent", "a1", "do it") in enterprise.calls


def test_tools_call_apply_and_find_skills():
    client, _ = _client()
    apply = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={
            "name": "apply_enterprise_skill",
            "arguments": {"skill_name": "Brand Voice", "text": "hi"},
        },
    )
    assert apply.json()["content"][0]["text"] == "Brand Voice: hi"

    find = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={"name": "find_enterprise_skills", "arguments": {"query": "tone"}},
    )
    assert "Brand Voice: bv" in find.json()["content"][0]["text"]


def test_unknown_tool_404():
    client, _ = _client()
    resp = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={"name": "nope", "arguments": {}},
    )
    assert resp.status_code == 404


def test_missing_argument_400():
    client, _ = _client()
    resp = client.post(
        "/mcp/tools/call",
        headers=_OK,
        json={"name": "search_enterprise", "arguments": {}},
    )
    assert resp.status_code == 400


def test_missing_token_rejected():
    client, _ = _client()
    assert client.post("/mcp/tools/list").status_code == 401
    assert (
        client.post(
            "/mcp/tools/call",
            json={"name": "search_enterprise", "arguments": {"query": "x"}},
        ).status_code
        == 401
    )


def test_invalid_token_rejected():
    client, _ = _client()
    bad = {"Authorization": "Bearer bad"}
    assert client.post("/mcp/tools/list", headers=bad).status_code == 401
