"""The interaction orchestrator — the session lifecycle for the wrapped world.

This is the repointed analogue of :mod:`a2a_workspace.session.lifecycle`. In the
local-materialized model, a "session" downloaded+verified skills and built a
credential-free local connection. In the wrapped model the runtime is a Google
Managed Agent, so the mapping becomes:

    workspace  -> per-user managed agent   (PerUserAgentProvisioner.ensure_agent)
    session    -> interaction              (platform.create_interaction)
    generation -> mounted SKILL_REGISTRY source on the agent

The orchestrator therefore does two things on each turn: ensure the user's agent
exists and is pointed at the right generation, then run one interaction against
it and hand back the streamed events. No credential ever flows through here; the
agent reaches enterprise data only via its MCP-server tool (the proxy boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from a2a_workspace.gemini_enterprise.agent_models import InteractionEvent
from a2a_workspace.identity.principal import Principal
from a2a_workspace.provisioning.agent_provisioner import PerUserAgentProvisioner


class InteractionPort(Protocol):
    """The interaction-running subset of the platform client.

    Local Protocol (not imported from the platform package) so the orchestrator
    is testable with a fake and never hard-binds the concrete client.
    """

    def create_interaction(
        self,
        *,
        agent: str,
        input: str,
        environment_type: str = "remote",
        stream: bool = True,
        store: bool = True,
        background: bool = False,
    ) -> list[InteractionEvent]: ...


@dataclass(frozen=True, slots=True)
class InteractionRun:
    agent_id: str
    events: list[InteractionEvent]


class InteractionOrchestrator:
    def __init__(
        self,
        provisioner: PerUserAgentProvisioner,
        platform: InteractionPort,
    ) -> None:
        self._provisioner = provisioner
        self._platform = platform

    def run(
        self,
        principal: Principal,
        *,
        input: str,
        skill_source: str | None = None,
        system_instruction: str = "",
        stream: bool = True,
        store: bool = True,
    ) -> InteractionRun:
        """Ensure the user's agent, then run one interaction against it.

        ``skill_source`` selects/activates a skill generation for this turn; when
        it differs from what is mounted the provisioner patches the agent first.
        """
        ensured = self._provisioner.ensure_agent(
            principal,
            skill_source=skill_source,
            system_instruction=system_instruction,
        )
        events = self._platform.create_interaction(
            agent=ensured.agent_id,
            input=input,
            stream=stream,
            store=store,
        )
        return InteractionRun(agent_id=ensured.agent_id, events=events)
