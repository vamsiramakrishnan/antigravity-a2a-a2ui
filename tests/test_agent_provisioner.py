from __future__ import annotations

from a2a_workspace.identity.principal import Principal
from a2a_workspace.provisioning.agent_provisioner import (
    AgentEnsureResult,
    AgentNotFound,
    PerUserAgentProvisioner,
    derive_agent_id,
)


# -- fake platform ---------------------------------------------------------


class FakeAgentPlatform:
    """Implements AgentPlatformPort; records create/patch; simulates existence."""

    def __init__(self, *, existing: dict | None = None, raise_missing: bool = True):
        # If existing is set, get_agent returns it; otherwise "missing".
        self._existing = existing
        self._raise_missing = raise_missing
        self.created: list = []
        self.patched: list = []
        self.get_calls: list[str] = []

    def get_agent(self, agent_id: str) -> dict:
        self.get_calls.append(agent_id)
        if self._existing is not None:
            return self._existing
        if self._raise_missing:
            raise AgentNotFound(agent_id)
        return {}

    def create_agent(self, spec) -> dict:
        self.created.append(spec)
        raw = {"name": f".../agents/{spec.id}"}
        # After creation it exists, so a subsequent ensure is idempotent.
        self._existing = spec_to_raw(spec)
        return raw

    def patch_agent(self, agent_id, *, base_environment=None, tools=None, system_instruction=None):
        self.patched.append(
            {
                "agent_id": agent_id,
                "base_environment": base_environment,
                "tools": tools,
                "system_instruction": system_instruction,
            }
        )
        return {"name": f".../agents/{agent_id}"}


def spec_to_raw(spec) -> dict:
    return spec.to_api()


def _principal(subject="user-1", email="alice@example.com") -> Principal:
    return Principal(issuer="https://idp", subject=subject, email=email)


def _provisioner(platform, **kw) -> PerUserAgentProvisioner:
    defaults = dict(
        gcs_source_for=lambda p: f"gs://bucket/workspaces/{p.key}/",
        mcp_server_url="https://gw/mcp",
        mcp_headers_provider=lambda p: {"Authorization": f"Bearer tok-{p.key}"},
    )
    defaults.update(kw)
    return PerUserAgentProvisioner(platform, **defaults)


# -- derive_agent_id -------------------------------------------------------


def test_derive_agent_id_deterministic():
    p = _principal()
    assert derive_agent_id(p) == derive_agent_id(p)


def test_derive_agent_id_resource_safe_and_no_pii():
    p = _principal(email="alice@example.com")
    aid = derive_agent_id(p)
    # lowercase alnum + hyphen only, starts with a letter.
    assert aid[0].isalpha()
    assert all(c.islower() or c.isdigit() or c == "-" for c in aid)
    # never contains the email or its local part.
    assert "alice" not in aid
    assert "@" not in aid
    assert "example" not in aid


def test_derive_agent_id_distinct_per_principal():
    assert derive_agent_id(_principal(subject="a")) != derive_agent_id(
        _principal(subject="b")
    )


def test_derive_agent_id_custom_prefix():
    assert derive_agent_id(_principal(), prefix="agt").startswith("agt-")


# -- first touch / create --------------------------------------------------


def test_create_on_first_touch_with_per_user_gcs_and_tools():
    platform = FakeAgentPlatform()  # missing -> raises AgentNotFound
    prov = _provisioner(platform)
    p = _principal()

    result = prov.ensure_agent(p, skill_source="gen-1")

    assert isinstance(result, AgentEnsureResult)
    assert result.created is True
    assert len(platform.created) == 1
    spec = platform.created[0]

    # agent id never contains the email.
    assert "alice" not in spec.id and "@" not in spec.id
    assert spec.id == derive_agent_id(p)

    body = spec.to_api()
    sources = body["base_environment"]["sources"]
    # per-user GCS source for THIS principal's prefix.
    gcs = [s for s in sources if s["type"] == "GCS"][0]
    assert gcs["source"] == f"gs://bucket/workspaces/{p.key}/"
    # skill registry source mounted.
    skill = [s for s in sources if s["type"] == "SKILL_REGISTRY"][0]
    assert skill["source"] == "gen-1"
    assert skill["target"] == "/.agent/skills"

    tool_types = [t["type"] for t in body["tools"]]
    assert tool_types == ["code_execution", "filesystem", "mcp_server"]
    mcp = [t for t in body["tools"] if t["type"] == "mcp_server"][0]
    assert mcp["url"] == "https://gw/mcp"
    assert mcp["headers"]["Authorization"] == f"Bearer tok-{p.key}"


def test_first_touch_without_skill_source_has_only_gcs():
    platform = FakeAgentPlatform()
    prov = _provisioner(platform)
    prov.ensure_agent(_principal())
    sources = platform.created[0].to_api()["base_environment"]["sources"]
    assert [s["type"] for s in sources] == ["GCS"]


# -- idempotency -----------------------------------------------------------


def test_idempotent_no_recreate_when_exists():
    platform = FakeAgentPlatform()
    prov = _provisioner(platform)
    p = _principal()

    first = prov.ensure_agent(p, skill_source="gen-1")
    second = prov.ensure_agent(p, skill_source="gen-1")

    assert first.created is True
    assert second.created is False
    assert len(platform.created) == 1
    assert platform.patched == []  # same generation -> no patch


def test_missing_signalled_by_falsy_return_also_works():
    # Port that returns {} instead of raising for "missing".
    platform = FakeAgentPlatform(raise_missing=False)
    prov = _provisioner(platform)
    result = prov.ensure_agent(_principal())
    assert result.created is True


# -- skill swap (activate generation) -------------------------------------


def test_skill_swap_patches_with_new_skill_source():
    # Existing agent already mounts gen-1.
    existing = {
        "name": ".../agents/x",
        "base_environment": {
            "sources": [
                {"type": "GCS", "source": "gs://bucket/...", "target": "/workspace"},
                {"type": "SKILL_REGISTRY", "source": "gen-1", "target": "/.agent/skills"},
            ]
        },
    }
    platform = FakeAgentPlatform(existing=existing)
    prov = _provisioner(platform)
    p = _principal()

    result = prov.ensure_agent(p, skill_source="gen-2")

    assert result.created is False
    assert platform.created == []
    assert len(platform.patched) == 1
    patch = platform.patched[0]
    assert patch["agent_id"] == derive_agent_id(p)
    # patched base_environment swaps the skill_registry source to gen-2.
    env = patch["base_environment"]
    body = env.to_api()
    skill = [s for s in body["sources"] if s["type"] == "SKILL_REGISTRY"][0]
    assert skill["source"] == "gen-2"
    assert skill["target"] == "/.agent/skills"


def test_same_skill_source_does_not_patch():
    existing = {
        "name": ".../agents/x",
        "base_environment": {
            "sources": [
                {"type": "SKILL_REGISTRY", "source": "gen-1", "target": "/.agent/skills"},
            ]
        },
    }
    platform = FakeAgentPlatform(existing=existing)
    prov = _provisioner(platform)
    result = prov.ensure_agent(_principal(), skill_source="gen-1")
    assert result.created is False
    assert platform.patched == []


# -- iam seam --------------------------------------------------------------


def test_iam_binder_called_on_create():
    calls = []
    platform = FakeAgentPlatform()
    prov = _provisioner(
        platform, agent_iam_binder=lambda *a: calls.append(a)
    )
    p = _principal()
    prov.ensure_agent(p)
    assert len(calls) == 1
    agent_id, key, gcs, email = calls[0]
    assert agent_id == derive_agent_id(p)
    assert key == p.key
    assert gcs == f"gs://bucket/workspaces/{p.key}/"


def test_gcs_prefix_template_alternative():
    platform = FakeAgentPlatform()
    prov = PerUserAgentProvisioner(
        platform,
        gcs_prefix_template="gs://b/{key}/",
        mcp_server_url="https://gw/mcp",
        mcp_headers_provider=lambda p: {},
    )
    p = _principal()
    prov.ensure_agent(p)
    gcs = [
        s for s in platform.created[0].to_api()["base_environment"]["sources"]
        if s["type"] == "GCS"
    ][0]
    assert gcs["source"] == f"gs://b/{p.key}/"
