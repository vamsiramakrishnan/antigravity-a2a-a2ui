from __future__ import annotations

from a2a_workspace.gemini_enterprise.agent_models import InteractionEvent
from a2a_workspace.identity.principal import Principal
from a2a_workspace.provisioning.agent_provisioner import AgentEnsureResult
from a2a_workspace.session.interaction import (
    InteractionOrchestrator,
    InteractionRun,
)


class FakeProvisioner:
    def __init__(self, agent_id="ws-deadbeef"):
        self.agent_id = agent_id
        self.calls: list[dict] = []

    def ensure_agent(self, principal, *, skill_source=None, system_instruction=""):
        self.calls.append(
            {
                "principal": principal,
                "skill_source": skill_source,
                "system_instruction": system_instruction,
            }
        )
        return AgentEnsureResult(agent_id=self.agent_id, created=True, raw={})


class FakePlatform:
    def __init__(self):
        self.calls: list[dict] = []

    def create_interaction(self, *, agent, input, environment_type="remote", stream=True, store=True, background=False):
        self.calls.append({"agent": agent, "input": input, "stream": stream, "store": store})
        return [
            InteractionEvent({"type": "text", "text": "Hello "}),
            InteractionEvent({"type": "text", "text": "world."}),
        ]


def _principal() -> Principal:
    return Principal(issuer="https://idp", subject="u", email="u@x.com")


def test_run_ensures_then_interacts_and_returns_events():
    prov = FakeProvisioner(agent_id="ws-abc123")
    platform = FakePlatform()
    orch = InteractionOrchestrator(prov, platform)

    run = orch.run(_principal(), input="hi", skill_source="gen-3")

    assert isinstance(run, InteractionRun)
    assert run.agent_id == "ws-abc123"
    assert [e.text for e in run.events] == ["Hello ", "world."]

    # ensure_agent was called with the generation + instruction.
    assert prov.calls[0]["skill_source"] == "gen-3"
    # interaction targeted the ensured agent id.
    assert platform.calls[0]["agent"] == "ws-abc123"
    assert platform.calls[0]["input"] == "hi"


def test_run_passes_system_instruction_and_flags():
    prov = FakeProvisioner()
    platform = FakePlatform()
    orch = InteractionOrchestrator(prov, platform)

    orch.run(
        _principal(),
        input="x",
        system_instruction="be terse",
        stream=False,
        store=False,
    )
    assert prov.calls[0]["system_instruction"] == "be terse"
    assert platform.calls[0]["stream"] is False
    assert platform.calls[0]["store"] is False


def test_ensure_runs_before_interaction():
    order: list[str] = []

    class OrderProv(FakeProvisioner):
        def ensure_agent(self, *a, **k):
            order.append("ensure")
            return super().ensure_agent(*a, **k)

    class OrderPlat(FakePlatform):
        def create_interaction(self, **k):
            order.append("interact")
            return super().create_interaction(**k)

    InteractionOrchestrator(OrderProv(), OrderPlat()).run(_principal(), input="x")
    assert order == ["ensure", "interact"]
