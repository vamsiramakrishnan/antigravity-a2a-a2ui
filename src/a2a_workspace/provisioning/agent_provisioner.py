"""Idempotent, first-touch provisioning of a per-user Managed Agent.

This is the wrapped-runtime analogue of
:mod:`a2a_workspace.provisioning.provisioner`. Where that module ensures a
workspace record + managed folder, this one ensures a *Google Managed Agent*
per end user, scoped to that user's GCS prefix and skills. The architecture
pivoted from materializing skills into a local runtime to wrapping one managed
agent **per principal**: isolation is enforced by *which* GCS prefix and skill
source we mount at provisioning time, never by handing the agent a credential.

``ensure_agent`` is safe to call on every authenticated invocation: it creates
the agent only if it does not already exist, and otherwise re-points the active
skill source if (and only if) the caller asked for a different generation. It
mirrors :meth:`WorkspaceProvisioner.ensure_provisioned`'s idempotent shape.

Seams kept behind Protocols/callables so this is unit-testable with fakes and
holds no concrete dependency on the platform client:

* :class:`AgentPlatformPort` — the subset of the teammate's
  ``AgentPlatformClient`` we use. We depend on the *shape*, not the class.
* ``agent_iam_binder`` — the per-agent IAM seam (mirrors ``FolderIamBinder``),
  default no-op. This is where the still-open "sandbox identity" question gets
  enforced once decided.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from a2a_workspace.gemini_enterprise.agent_models import (
    AgentSpec,
    AgentTool,
    BaseEnvironment,
    EnvironmentSource,
    NetworkConfig,
)
from a2a_workspace.identity.principal import Principal

# Where a skill generation is mounted inside the agent's filesystem. The agent's
# runtime reads its skills from here, so swapping this source's ``source`` value
# is what "activates a generation".
DEFAULT_SKILL_TARGET = "/.agent/skills"

# Resource ids on the platform must be DNS/label-safe: lowercase alnum + hyphen.
_ID_SAFE = re.compile(r"[^a-z0-9-]")

# (agent_id, principal_key, gcs_prefix, member_email|None) -> None. The per-agent
# IAM seam. In production this binds the agent's runtime/sandbox identity to the
# user's GCS prefix only. Mirrors ``FolderIamBinder`` from provisioner.py.
AgentIamBinder = Callable[[str, str, str, "str | None"], None]

# principal -> a GCS source URI (e.g. "gs://bucket/workspaces/<id>/"). Lets the
# composition root decide how a principal maps to storage without this module
# naming a bucket. Either pass this callable or a ``gcs_prefix_template``.
GcsSourceFor = Callable[[Principal], str]

# session-scoped proxy token provider for the MCP server callback, per principal.
McpHeadersProvider = Callable[[Principal], "dict[str, str]"]


class AgentNotFound(Exception):
    """Raised by a platform port when an agent does not exist.

    Provisioning treats this as "not created yet" rather than an error. We define
    it here so both the real client wrapper and test fakes have a single, typed
    signal to raise; ``ensure_agent`` also tolerates a falsy ``get_agent`` return
    as "missing" so a port that prefers that convention still works.
    """


class AgentPlatformPort(Protocol):
    """The subset of ``AgentPlatformClient`` this provisioner depends on.

    Defined locally (not imported from the platform package) so core logic stays
    testable with a fake and does not hard-bind to the concrete client.
    """

    def get_agent(self, agent_id: str) -> dict: ...

    def create_agent(self, spec: AgentSpec) -> dict: ...

    def patch_agent(
        self,
        agent_id: str,
        *,
        base_environment=None,
        tools=None,
        system_instruction: str | None = None,
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class AgentEnsureResult:
    agent_id: str
    created: bool
    raw: dict


def derive_agent_id(principal: Principal, *, prefix: str = "ws") -> str:
    """Deterministically derive a DNS/resource-safe agent id for a principal.

    Why a hash and not the email (or any PII)?

    * **Stability** — the id must be identical across calls so ``ensure_agent``
      is idempotent without a registry round-trip. ``principal.key`` is already a
      stable hash of the verified ``(issuer, subject)``; we slug a slice of it.
    * **No PII / no attacker-influenced data** — email is mutable, reassignable,
      and frequently arrives inside a prompt or A2A body (i.e. untrusted). It must
      never end up in a resource name that participates in isolation decisions.
    * **Resource-safe** — managed-agent ids are DNS-label-ish: lowercase
      alphanumerics and hyphens, must start with a letter. We prefix and trim.

    The result looks like ``ws-1a2b3c4d5e6f7a8b`` and is short, opaque, stable.
    """
    # principal.key is "prn_<32 hex>"; take the hex tail and re-hash for an even
    # spread, then keep a short slug. Re-hashing avoids leaking the key verbatim.
    digest = hashlib.sha256(principal.key.encode()).hexdigest()[:16]
    safe_prefix = _ID_SAFE.sub("-", prefix.lower()).strip("-") or "ws"
    return f"{safe_prefix}-{digest}"


class PerUserAgentProvisioner:
    """Ensures exactly one managed agent per principal, scoped to their prefix.

    The agent's base environment mounts (a) the user's own GCS prefix and (b),
    optionally, the active skill generation as a ``SKILL_REGISTRY`` source. Its
    tools are code execution, filesystem, and the enterprise-proxy MCP server —
    the MCP server is how the sandboxed agent reaches Gemini Enterprise without
    ever holding the user's credential (see :mod:`gateway.mcp_server`).
    """

    def __init__(
        self,
        platform: AgentPlatformPort,
        *,
        base_agent: str = "antigravity-preview-05-2026",
        gcs_source_for: GcsSourceFor | None = None,
        gcs_prefix_template: str | None = None,
        mcp_server_url: str,
        mcp_headers_provider: McpHeadersProvider,
        mcp_server_name: str = "enterprise",
        skill_target: str = DEFAULT_SKILL_TARGET,
        gcs_target: str = "/workspace",
        network_domains: tuple[str, ...] = ("*",),
        agent_iam_binder: AgentIamBinder | None = None,
        agent_id_prefix: str = "ws",
    ) -> None:
        if gcs_source_for is None and gcs_prefix_template is None:
            raise ValueError(
                "provide either gcs_source_for (callable) or gcs_prefix_template"
            )
        self._platform = platform
        self._base_agent = base_agent
        self._gcs_source_for = gcs_source_for
        self._gcs_prefix_template = gcs_prefix_template
        self._mcp_url = mcp_server_url
        self._mcp_headers = mcp_headers_provider
        self._mcp_name = mcp_server_name
        self._skill_target = skill_target
        self._gcs_target = gcs_target
        self._network_domains = network_domains
        # Default no-op IAM binder: storage isolation is enforced by *which*
        # prefix we mount, not by per-agent IAM here. The seam exists so the
        # open "sandbox identity" decision drops in without touching call sites.
        self._iam = agent_iam_binder or _noop_agent_iam
        self._id_prefix = agent_id_prefix

    # -- public ------------------------------------------------------------

    def ensure_agent(
        self,
        principal: Principal,
        *,
        skill_source: str | None = None,
        system_instruction: str = "",
    ) -> AgentEnsureResult:
        agent_id = derive_agent_id(principal, prefix=self._id_prefix)
        gcs_source = self._gcs_source(principal)

        existing = self._get_or_none(agent_id)
        if existing is None:
            spec = self._build_spec(
                principal,
                agent_id=agent_id,
                gcs_source=gcs_source,
                skill_source=skill_source,
                system_instruction=system_instruction,
            )
            raw = self._platform.create_agent(spec)
            # Bind the agent's runtime identity to this user's prefix only.
            self._iam(agent_id, principal.key, gcs_source, principal.email)
            return AgentEnsureResult(agent_id=agent_id, created=True, raw=raw)

        # Already provisioned. If the caller asked for a specific generation and
        # it differs from what is mounted, re-point the skill source ("activate
        # generation") via patch. Otherwise leave it untouched (idempotent).
        if skill_source is not None and self._skill_differs(existing, skill_source):
            new_env = self._build_environment(
                gcs_source=gcs_source, skill_source=skill_source
            )
            raw = self._platform.patch_agent(agent_id, base_environment=new_env)
            return AgentEnsureResult(agent_id=agent_id, created=False, raw=raw)

        return AgentEnsureResult(agent_id=agent_id, created=False, raw=existing)

    # -- spec building -----------------------------------------------------

    def _build_spec(
        self,
        principal: Principal,
        *,
        agent_id: str,
        gcs_source: str,
        skill_source: str | None,
        system_instruction: str,
    ) -> AgentSpec:
        return AgentSpec(
            id=agent_id,
            base_agent=self._base_agent,
            description=f"Per-user Antigravity agent ({agent_id})",
            system_instruction=system_instruction,
            tools=self._build_tools(principal),
            base_environment=self._build_environment(
                gcs_source=gcs_source, skill_source=skill_source
            ),
        )

    def _build_tools(self, principal: Principal) -> tuple[AgentTool, ...]:
        return (
            AgentTool.code_execution(),
            AgentTool.filesystem(),
            AgentTool.mcp_server(
                self._mcp_name,
                self._mcp_url,
                headers=self._mcp_headers(principal),
            ),
        )

    def _build_environment(
        self, *, gcs_source: str, skill_source: str | None
    ) -> BaseEnvironment:
        sources: list[EnvironmentSource] = [
            EnvironmentSource(type="GCS", source=gcs_source, target=self._gcs_target)
        ]
        if skill_source is not None:
            sources.append(
                EnvironmentSource(
                    type="SKILL_REGISTRY",
                    source=skill_source,
                    target=self._skill_target,
                )
            )
        return BaseEnvironment(
            sources=tuple(sources),
            network=NetworkConfig.allow_domains(*self._network_domains),
        )

    # -- helpers -----------------------------------------------------------

    def _gcs_source(self, principal: Principal) -> str:
        if self._gcs_source_for is not None:
            return self._gcs_source_for(principal)
        assert self._gcs_prefix_template is not None  # guarded in __init__
        # Template gets the stable workspace key; never the email.
        return self._gcs_prefix_template.format(key=principal.key)

    def _get_or_none(self, agent_id: str) -> dict | None:
        """Return the agent dict, or ``None`` if it does not exist.

        We accept *two* "missing" conventions so either a real client wrapper or
        a fake works: raising :class:`AgentNotFound`, or returning a falsy value.
        """
        try:
            raw = self._platform.get_agent(agent_id)
        except AgentNotFound:
            return None
        return raw or None

    @staticmethod
    def _skill_differs(existing: dict, skill_source: str) -> bool:
        """True if the agent's mounted SKILL_REGISTRY source != ``skill_source``."""
        env = existing.get("base_environment") or {}
        for src in env.get("sources", []) or ():
            if src.get("type") == "SKILL_REGISTRY":
                return src.get("source") != skill_source
        # No skill source mounted yet but one was requested -> differs.
        return True


def _noop_agent_iam(
    agent_id: str, principal_key: str, gcs_source: str, member_email: str | None
) -> None:
    # Local/dev: the sandbox identity question is unresolved, so binding is a
    # recorded no-op. Production wiring replaces this without touching callers.
    return None
