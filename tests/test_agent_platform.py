from __future__ import annotations

import json

import pytest

from a2a_workspace.gemini_enterprise.agent_models import (
    AgentSpec,
    AgentTool,
    BaseEnvironment,
    EnvironmentSource,
    InteractionEvent,
    NetworkConfig,
)
from a2a_workspace.gemini_enterprise.agent_platform import AgentPlatformClient
from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.transport import HttpResponse


# -- fake transport --------------------------------------------------------


class FakeTransport:
    """Captures the last request and returns canned responses by URL/method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.last_method = ""
        self.last_url = ""
        self.last_headers: dict = {}
        self.last_body = None

    def request(self, method, url, *, headers, body=None) -> HttpResponse:
        self.calls.append((method, url))
        self.last_method = method
        self.last_url = url
        self.last_headers = headers
        self.last_body = json.loads(body) if body else None
        assert headers["Authorization"].startswith("Bearer ")

        if url.endswith(":interact"):
            events = [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world."},
            ]
            return HttpResponse(200, json.dumps(events).encode())
        if method == "GET" and url.rstrip("/").endswith("/agents"):
            return HttpResponse(
                200,
                json.dumps(
                    {"agents": [{"name": ".../agents/agent1"}, {"name": ".../agents/agent2"}]}
                ).encode(),
            )
        if method == "GET":  # get_agent
            return HttpResponse(200, json.dumps({"name": ".../agents/agent1"}).encode())
        if method == "POST":  # create_agent
            return HttpResponse(200, json.dumps({"name": ".../agents/agent1"}).encode())
        if method == "PATCH":
            return HttpResponse(200, json.dumps({"name": ".../agents/agent1"}).encode())
        if method == "DELETE":
            return HttpResponse(200, b"{}")
        return HttpResponse(404, b"{}")


def _config(location: str = "us-central1") -> GeminiEnterpriseConfig:
    return GeminiEnterpriseConfig(project="p", location=location, engine="app")


def _client(transport, location: str = "us-central1") -> AgentPlatformClient:
    return AgentPlatformClient(_config(location), "user-tok", transport)


def _spec() -> AgentSpec:
    return AgentSpec(
        id="my-agent",
        description="does things",
        system_instruction="be helpful",
        tools=(AgentTool.code_execution(), AgentTool.google_search()),
        base_environment=BaseEnvironment(
            sources=(
                EnvironmentSource(type="GCS", source="gs://b/x", target="/work"),
            ),
            network=NetworkConfig.allow_domains("*"),
        ),
    )


# -- construction ----------------------------------------------------------


def test_refuses_empty_token():
    with pytest.raises(ValueError):
        AgentPlatformClient(_config(), "")


# -- create ----------------------------------------------------------------


def test_create_agent_body_and_agent_id_query():
    t = FakeTransport()
    client = _client(t)
    client.create_agent(_spec())

    method, url = t.calls[-1]
    assert method == "POST"
    assert "agent_id=my-agent" in url
    assert url.endswith("/agents?agent_id=my-agent")
    # Body carries name=id and the spec fields.
    assert t.last_body["name"] == "my-agent"
    assert t.last_body["base_agent"] == "antigravity-preview-05-2026"
    assert t.last_body["description"] == "does things"
    assert t.last_body["system_instruction"] == "be helpful"
    assert t.last_body["tools"][0]["type"] == "code_execution"
    assert t.last_body["base_environment"]["network"]["allowlist"] == [
        {"domain": "*"}
    ]


def test_agentspec_to_api_shape_matches_sample():
    body = _spec().to_api()
    assert set(body) == {
        "name",
        "base_agent",
        "description",
        "system_instruction",
        "tools",
        "base_environment",
    }
    assert body["name"] == "my-agent"
    src = body["base_environment"]["sources"][0]
    assert src == {"type": "GCS", "target": "/work", "source": "gs://b/x"}


def test_environment_source_has_no_credential_field():
    # Scope is enforced by what is mounted, not a per-source secret.
    src = EnvironmentSource(type="GCS", source="gs://b/x", target="/w")
    assert "credential" not in src.to_api()
    assert "principal" not in src.to_api()


def test_agent_tool_constructors():
    assert AgentTool.url_context().to_api() == {"type": "url_context"}
    assert AgentTool.filesystem().to_api() == {"type": "filesystem"}
    mcp = AgentTool.mcp_server("srv", "https://m", {"X": "1"}).to_api()
    assert mcp == {
        "type": "mcp_server",
        "name": "srv",
        "url": "https://m",
        "headers": {"X": "1"},
    }


# -- patch -----------------------------------------------------------------


def test_patch_update_mask_only_provided_fields():
    t = FakeTransport()
    client = _client(t)
    client.patch_agent("my-agent", system_instruction="new instruction")

    method, url = t.calls[-1]
    assert method == "PATCH"
    assert "update_mask=system_instruction" in url
    assert "tools" not in url
    assert "base_environment" not in url
    assert t.last_body == {"system_instruction": "new instruction"}


def test_patch_multiple_fields_mask():
    t = FakeTransport()
    client = _client(t)
    client.patch_agent(
        "my-agent",
        tools=(AgentTool.google_search(),),
        base_environment=BaseEnvironment(),
    )
    _, url = t.calls[-1]
    assert "update_mask=" in url
    mask = url.split("update_mask=")[1]
    assert set(mask.split(",")) == {"base_environment", "tools"}
    assert t.last_body["tools"] == [{"type": "google_search"}]


# -- get / list / delete ---------------------------------------------------


def test_list_returns_agents_array():
    t = FakeTransport()
    client = _client(t)
    agents = client.list_agents()
    assert [a["name"] for a in agents] == [".../agents/agent1", ".../agents/agent2"]


def test_list_empty_when_absent():
    class Empty(FakeTransport):
        def request(self, method, url, *, headers, body=None):
            super().request(method, url, headers=headers, body=body)
            return HttpResponse(200, b"{}")

    assert _client(Empty()).list_agents() == []


def test_get_agent():
    t = FakeTransport()
    assert _client(t).get_agent("agent1")["name"] == ".../agents/agent1"
    assert t.last_method == "GET"


def test_delete_agent():
    t = FakeTransport()
    assert _client(t).delete_agent("agent1") is None
    assert t.last_method == "DELETE"


# -- host selection --------------------------------------------------------


def test_regional_host():
    client = _client(FakeTransport(), location="us-central1")
    assert client.base_url == "https://us-central1-aiplatform.googleapis.com/v1beta1"
    assert client.parent == "projects/p/locations/us-central1"


def test_global_host_non_regional():
    client = _client(FakeTransport(), location="global")
    assert client.base_url == "https://aiplatform.googleapis.com/v1beta1"
    assert client.parent == "projects/p/locations/global"


# -- interactions (REST fallback) ------------------------------------------


def test_create_interaction_rest_fallback_request_shape():
    t = FakeTransport()
    client = _client(t)
    events = client.create_interaction(agent="my-agent", input="hi there")

    method, url = t.calls[-1]
    assert method == "POST"
    assert url.endswith("/agents/my-agent:interact")
    assert t.last_body == {
        "input": "hi there",
        "environment": {"type": "remote"},
        "stream": True,
        "store": True,
        "background": False,
    }
    # Events wrapped with convenience accessors.
    assert [e.text for e in events] == ["Hello ", "world."]
    assert events[0].type == "text"


def test_create_interaction_full_resource_name_agent():
    t = FakeTransport()
    client = _client(t)
    client.create_interaction(
        agent="projects/p/locations/us-central1/agents/abc", input="x"
    )
    _, url = t.calls[-1]
    assert url.endswith("/agents/abc:interact")


def test_interaction_event_accessors():
    assert InteractionEvent({"text": "hi"}).text == "hi"
    nested = InteractionEvent({"content": {"parts": [{"text": "a"}, {"text": "b"}]}})
    assert nested.text == "ab"
    assert InteractionEvent({"event_type": "status"}).type == "status"
