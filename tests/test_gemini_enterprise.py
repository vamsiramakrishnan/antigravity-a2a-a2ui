from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from a2a_workspace.config import (
    Config,
    IdentityConfig,
    RegistryConfig,
    SessionConfig,
    StorageConfig,
)
from a2a_workspace.container import build_container
from a2a_workspace.gateway.app import create_app
from a2a_workspace.gemini_enterprise.client import DiscoveryEngineClient
from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.skill import generate_skill_bundle
from a2a_workspace.gemini_enterprise.tools import make_enterprise_tools
from a2a_workspace.gemini_enterprise.transport import HttpResponse, Transport
from a2a_workspace.identity.session_token import SessionTokenService


# -- fakes -----------------------------------------------------------------


class FakeDiscoveryTransport:
    """Stand-in for discoveryengine.googleapis.com."""

    def __init__(self) -> None:
        self.last_url = ""
        self.last_body = None

    def request(self, method, url, *, headers, body=None) -> HttpResponse:
        self.last_url = url
        self.last_body = json.loads(body) if body else None
        assert headers["Authorization"].startswith("Bearer ")
        if url.endswith(":streamAssist"):
            chunks = [
                {"answer": {"replies": [{"text": "Hello "}], "state": "IN_PROGRESS"}},
                {
                    "answer": {
                        "replies": [{"text": "world."}],
                        "citations": [{"title": "Doc A", "uri": "https://a"}],
                        "state": "SUCCEEDED",
                    },
                    "sessionInfo": {"session": "projects/p/.../sessions/123"},
                },
            ]
            return HttpResponse(200, json.dumps(chunks).encode())
        if url.endswith("/agents"):
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "agents": [
                            {
                                "name": "projects/p/.../assistants/default_assistant/agents/agent1",
                                "displayName": "Agent One",
                                "description": "A specialized agent",
                            }
                        ]
                    }
                ).encode(),
            )
        return HttpResponse(404, b"{}")


def _ge_config() -> GeminiEnterpriseConfig:
    return GeminiEnterpriseConfig(project="p", location="global", engine="app")


# -- client ----------------------------------------------------------------


def test_assist_aggregates_streamed_answer_and_citations():
    transport = FakeDiscoveryTransport()
    client = DiscoveryEngineClient(
        config=_ge_config(), access_token="user-tok", transport=transport
    )
    result = client.assist("what is X?")
    assert "Hello world." in result.answer
    assert result.citations[0].title == "Doc A"
    assert "Sources:" in result.as_text()
    # The request used the streamAssist endpoint with the query text.
    assert transport.last_url.endswith(":streamAssist")
    assert transport.last_body["query"]["text"] == "what is X?"


def test_invoke_agent_sets_agents_spec():
    transport = FakeDiscoveryTransport()
    client = DiscoveryEngineClient(
        config=_ge_config(), access_token="user-tok", transport=transport
    )
    client.invoke_agent("agent1", "do the thing")
    agent = transport.last_body["agentsSpec"]["agentSpecs"][0]["agent"]
    assert agent.endswith("/agents/agent1")


def test_list_agents_parses_response():
    client = DiscoveryEngineClient(
        config=_ge_config(), access_token="t", transport=FakeDiscoveryTransport()
    )
    agents = client.list_agents()
    assert agents[0].agent_id == "agent1"
    assert agents[0].display_name == "Agent One"


def test_client_refuses_empty_token():
    with pytest.raises(ValueError):
        DiscoveryEngineClient(config=_ge_config(), access_token="")


# -- session token ---------------------------------------------------------


def test_session_token_roundtrip_and_tamper():
    svc = SessionTokenService(secret=b"x" * 32, ttl_seconds=60)
    tok = svc.mint(principal_key="prn_abc", conversation_id="conv_1")
    parsed = svc.verify(f"Bearer {tok}")
    assert parsed.principal_key == "prn_abc"
    assert parsed.conversation_id == "conv_1"
    from a2a_workspace.errors import AuthorizationError

    with pytest.raises(AuthorizationError):
        svc.verify(tok + "tampered")


# -- skill bundle ----------------------------------------------------------


def test_skill_bundle_has_expected_files():
    bundle = generate_skill_bundle()
    assert "SKILL.md" in bundle
    assert "manifest.json" in bundle
    assert b"get_tools" in bundle["tools/enterprise_tools.py"]
    manifest = json.loads(bundle["manifest.json"])
    assert manifest["name"] == "gemini-enterprise-connectors"


# -- end-to-end proxy: tool -> gateway -> discovery engine -----------------


def _config(tmp_path) -> Config:
    return Config(
        organization="acme",
        environment="test",
        storage=StorageConfig(backend="local", local_root=str(tmp_path / "bucket")),
        identity=IdentityConfig(backend="dev", allow_insecure_dev=True),
        registry=RegistryConfig(backend="memory"),
        session=SessionConfig(materialization_root=str(tmp_path / "sessions")),
        gemini=_ge_config(),
        public_url="http://testserver",
        session_token_secret="0123456789abcdef0123456789abcdef",
    )


class GatewayProxyTransport:
    """Routes the proxy tools' HTTP calls through the in-process FastAPI app."""

    def __init__(self, client: TestClient, base: str) -> None:
        self._client = client
        self._base = base

    def request(self, method, url, *, headers, body=None) -> HttpResponse:
        path = url[len(self._base) :] if url.startswith(self._base) else url
        resp = self._client.request(method, path, headers=headers, content=body)
        return HttpResponse(resp.status_code, resp.content)


def test_proxy_tools_reach_discovery_engine_through_gateway(tmp_path):
    config = _config(tmp_path)
    discovery = FakeDiscoveryTransport()
    container = build_container(config, discovery_transport=discovery)
    app = create_app(config, container=container)
    client = TestClient(app)

    # Simulate what /a2a/invoke does: mint a session token + stash the user's
    # Discovery Engine credential server-side for the conversation.
    conversation_id = "conv_test"
    session_token = container.session_tokens.mint(
        principal_key="prn_user", conversation_id=conversation_id
    )
    container.session_credentials.put(conversation_id, "user-ge-token")

    tools = make_enterprise_tools(
        gateway_url="http://testserver",
        token_provider=lambda: session_token,
        transport=GatewayProxyTransport(client, "http://testserver"),
    )
    by_name = {t.__name__: t for t in tools}

    # The agent calls search_enterprise; it proxies through the gateway, which
    # uses the stored user token to call Discovery Engine.
    answer = by_name["search_enterprise"]("what is X?")
    assert "Hello world." in answer
    # The user's token reached Discovery Engine; the tool never saw it.
    assert discovery.last_body["query"]["text"] == "what is X?"

    agents = by_name["list_enterprise_agents"]()
    assert "agent1" in agents

    delegated = by_name["invoke_enterprise_agent"]("agent1", "do it")
    assert "Hello world." in delegated


def test_enterprise_endpoint_fails_closed_without_user_credential(tmp_path):
    config = _config(tmp_path)
    container = build_container(config, discovery_transport=FakeDiscoveryTransport())
    app = create_app(config, container=container)
    client = TestClient(app)

    # Valid session token, but no stored user credential -> 403, not an ambient call.
    token = container.session_tokens.mint(
        principal_key="prn_user", conversation_id="conv_missing"
    )
    resp = client.post(
        "/enterprise/assist",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "x"},
    )
    assert resp.status_code == 403


def test_invoke_provisions_enterprise_session(tmp_path):
    config = _config(tmp_path)
    container = build_container(config, discovery_transport=FakeDiscoveryTransport())
    app = create_app(config, container=container)
    client = TestClient(app)
    headers = {"Authorization": "Bearer https://idp|author|a@x.com"}

    # Publish a skill so the workspace has an active generation to materialize.
    did = client.post("/workspaces/me/drafts", headers=headers, json={}).json()[
        "draft_id"
    ]
    for path, data in generate_skill_bundle().items():
        client.put(
            f"/workspaces/me/drafts/{did}/files",
            headers=headers,
            json={"path": path, "content_base64": _b64(data)},
        )
    client.post(f"/workspaces/me/drafts/{did}/validate", headers=headers)
    client.post(f"/workspaces/me/drafts/{did}/submit", headers=headers)
    client.post(f"/workspaces/me/drafts/{did}/publish", headers=headers, json={})

    # Invoke WITH a delegated user token (toolAuthorization plane).
    invoked = client.post(
        "/a2a/invoke",
        headers={**headers, "X-Tool-Authorization": "user-ge-token"},
    ).json()

    assert invoked["enterprise"]["enabled"] is True
    # The session file exists and carries a session token (not the user token).
    import json as _json
    from pathlib import Path

    session_file = Path(invoked["enterprise"]["session_file"])
    assert session_file.is_file()
    info = _json.loads(session_file.read_text())
    assert info["session_token"]
    assert "user-ge-token" not in session_file.read_text()
    # The user's credential is held server-side for the conversation.
    assert (
        container.session_credentials.get(invoked["conversation_id"]) == "user-ge-token"
    )


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


def test_enterprise_endpoint_rejects_bad_session_token(tmp_path):
    config = _config(tmp_path)
    container = build_container(config, discovery_transport=FakeDiscoveryTransport())
    client = TestClient(create_app(config, container=container))
    resp = client.post(
        "/enterprise/assist",
        headers={"Authorization": "Bearer not-a-real-token"},
        json={"query": "x"},
    )
    assert resp.status_code == 401
