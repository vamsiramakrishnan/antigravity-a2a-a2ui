"""Tests for the agents-cli integration surface.

Covers the manifest, the pure adapter/command helpers, the FastAPI compat router
that serves the A2A card, and that the ADK agent spec imports without google-adk.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from a2a_workspace.integrations.agents_cli import (
    WELL_KNOWN_AGENT_CARD_PATH,
    agent_card_to_a2a,
    build_publish_command,
    discoveryengine_invoker_member,
    manifest_dict,
)
from a2a_workspace.gateway.agents_cli_compat import create_agents_cli_compat_router

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "agents-cli-manifest.yaml"


# A representative gateway card (the shape a2a_workspace.gateway.a2a.agent_card
# returns), used to exercise the adapter and the compat router without spinning
# up the whole container.
SAMPLE_GATEWAY_CARD = {
    "name": "acme-antigravity-workspace",
    "description": "Per-user Antigravity skill workspace over A2A/A2UI.",
    "version": "0.1.0",
    "capabilities": {"a2ui": True, "streaming": False},
    "agentAuthorization": {"type": "oauth2"},
    "toolAuthorizations": [{"id": "workspace-storage", "type": "oauth2"}],
    "skills": [
        {
            "id": "enterprise-search",
            "name": "Search enterprise data",
            "description": "Answer from connectors with citations.",
        }
    ],
}


def _load_manifest() -> dict:
    """Parse the manifest with PyYAML if available, else a tiny line fallback."""
    text = MANIFEST_PATH.read_text()
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:  # pragma: no cover - yaml present in this venv
        # Minimal fallback: extract the keys the test asserts on by line.
        data: dict = {"create_params": {}}
        in_params = False
        for raw in text.splitlines():
            if raw.strip().startswith("#") or not raw.strip():
                continue
            if raw.startswith("create_params:"):
                in_params = True
                continue
            if ":" not in raw:
                continue
            key, _, val = raw.strip().partition(":")
            val = val.strip().strip('"')
            if val in ("true", "false"):
                val = val == "true"
            target = data["create_params"] if (in_params and raw.startswith(" ")) else data
            target[key] = val
        return data


def test_manifest_parses_and_has_required_keys():
    manifest = _load_manifest()
    for key in (
        "name",
        "acli_version",
        "agent_directory",
        "region",
        "base_template",
        "generated_at",
        "language",
        "create_params",
    ):
        assert key in manifest, f"missing top-level key: {key}"

    assert manifest["name"] == "antigravity-a2a-a2ui"
    assert manifest["language"] == "python"
    assert manifest["agent_directory"] == "app"
    assert manifest["base_template"] == "adk_a2a"

    params = manifest["create_params"]
    assert params["is_a2a"] is True
    assert params["deployment_target"] == "cloud_run"
    assert params["include_data_ingestion"] is False
    assert params["session_type"] == "none"
    assert params["datastore"] == "none"


def test_manifest_dict_mirrors_yaml():
    manifest = _load_manifest()
    mirror = manifest_dict()
    assert mirror["name"] == manifest["name"]
    assert mirror["create_params"]["is_a2a"] == manifest["create_params"]["is_a2a"]
    assert (
        mirror["create_params"]["deployment_target"]
        == manifest["create_params"]["deployment_target"]
    )


def test_agent_card_to_a2a_required_fields():
    a2a = agent_card_to_a2a(SAMPLE_GATEWAY_CARD, base_url="https://gw.example.com/")

    assert a2a["name"] == "acme-antigravity-workspace"
    assert a2a["description"]
    assert a2a["version"] == "0.1.0"
    # url points at the gateway invoke endpoint, trailing slash normalized.
    assert a2a["url"] == "https://gw.example.com/a2a/invoke"

    caps = a2a["capabilities"]
    assert caps["streaming"] is False
    assert caps["pushNotifications"] is False
    assert caps["stateTransitionHistory"] is False
    assert caps["a2ui"] is True  # additive flag preserved

    assert a2a["defaultInputModes"] == ["text/plain"]
    assert a2a["defaultOutputModes"] == ["text/plain"]

    skill = a2a["skills"][0]
    assert skill["id"] == "enterprise-search"
    assert skill["name"] == "Search enterprise data"
    assert skill["tags"] == []
    assert skill["inputModes"] == ["text/plain"]


def test_build_publish_command_is_exact():
    argv = build_publish_command(
        base_url="https://gw.example.com",
        app_engine_id=(
            "projects/123/locations/global/collections/default_collection"
            "/engines/my-app"
        ),
        display_name="Antigravity Skill Assistant",
    )
    assert argv == [
        "agents-cli",
        "publish",
        "gemini-enterprise",
        "--registration-type",
        "a2a",
        "--agent-card-url",
        "https://gw.example.com/a2a/app/.well-known/agent-card.json",
        "--gemini-enterprise-app-id",
        "projects/123/locations/global/collections/default_collection/engines/my-app",
        "--display-name",
        "Antigravity Skill Assistant",
    ]
    # The card URL suffix is the agents-cli/Discovery Engine well-known path.
    assert argv[6].endswith(WELL_KNOWN_AGENT_CARD_PATH)
    assert WELL_KNOWN_AGENT_CARD_PATH == "/a2a/app/.well-known/agent-card.json"


def test_build_publish_command_includes_description_when_given():
    argv = build_publish_command(
        base_url="https://gw.example.com",
        app_engine_id="projects/1/locations/global/collections/c/engines/e",
        display_name="X",
        description="a per-user skill assistant",
    )
    assert "--description" in argv
    assert argv[argv.index("--description") + 1] == "a per-user skill assistant"


def test_discoveryengine_invoker_member_format():
    member = discoveryengine_invoker_member(123456789)
    assert member == (
        "serviceAccount:service-123456789"
        "@gcp-sa-discoveryengine.iam.gserviceaccount.com"
    )
    # Accepts strings too.
    assert discoveryengine_invoker_member("42").startswith(
        "serviceAccount:service-42@"
    )


def test_compat_router_serves_card_at_both_paths():
    app = FastAPI()
    router = create_agents_cli_compat_router(
        card_provider=lambda: SAMPLE_GATEWAY_CARD,
        base_url="https://gw.example.com",
    )
    app.include_router(router)
    client = TestClient(app)

    for path in (
        "/a2a/app/.well-known/agent-card.json",
        "/.well-known/agent-card.json",
    ):
        resp = client.get(path)
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "acme-antigravity-workspace"
        assert card["url"] == "https://gw.example.com/a2a/invoke"
        assert card["capabilities"]["streaming"] is False


def test_root_agent_spec_importable_without_adk():
    # Importing app.agent must not require google-adk.
    from app.agent import ROOT_AGENT_SPEC, build_tools, root_agent

    assert ROOT_AGENT_SPEC["name"] == "antigravity_skill_assistant"
    assert ROOT_AGENT_SPEC["model"]
    assert ROOT_AGENT_SPEC["instruction"]
    assert "search_enterprise" in ROOT_AGENT_SPEC["tools"]
    # google-adk absent here -> root_agent is None but the module still imported.
    assert root_agent is None

    # build_tools works without ADK (the tools are plain callables).
    tools = build_tools(gateway_url="https://gw", token_provider=lambda: "t")
    names = {t.__name__ for t in tools}
    assert set(ROOT_AGENT_SPEC["tools"]) <= names
