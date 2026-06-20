"""Typed request/response models for the Managed Agents platform.

These mirror the Vertex AI ``aiplatform`` v1beta1 Agents API
(``projects/.../locations/.../agents``) and its companion Interactions API.
They follow the two-layer pattern used throughout this package: a *spec* the
caller composes declaratively (frozen dataclasses, no I/O), and a *realizer*
(:class:`~a2a_workspace.gemini_enterprise.agent_platform.AgentPlatformClient`)
that turns a spec into HTTP calls. Keeping the wire shape in one place — the
``to_api`` methods — means a contract change is a localized edit, and the client
stays a thin transport.

Every model is ``frozen=True, slots=True``: specs are values, not handles, so
they are cheap to compare in tests and safe to share.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class EnvironmentSource:
    """One mount into an agent's base environment.

    A source declares *what* to make available (a GCS prefix, an inline blob, a
    git repository, or a skill from the registry) and *where* it lands inside the
    agent's filesystem (``target``).

    There is deliberately **no** credential, principal, or per-source secret
    field. Access scope is enforced by *which* source/prefix is mounted at
    provisioning time — the control plane only ever mounts what the calling user
    may reach — not by attaching a secret to each source. Putting a credential
    here would invite a confused-deputy: an agent definition that out-lives the
    user who authored it could be replayed against the embedded secret.
    """

    type: Literal["GCS", "INLINE", "REPOSITORY", "SKILL_REGISTRY"]
    target: str
    source: str | None = None
    content: str | None = None
    encoding: str | None = None

    def to_api(self) -> dict:
        body: dict = {"type": self.type, "target": self.target}
        if self.source is not None:
            body["source"] = self.source
        if self.content is not None:
            body["content"] = self.content
        if self.encoding is not None:
            body["encoding"] = self.encoding
        return body

    @classmethod
    def from_api(cls, d: dict) -> "EnvironmentSource":
        return cls(
            type=d.get("type", "GCS"),
            target=d.get("target", ""),
            source=d.get("source"),
            content=d.get("content"),
            encoding=d.get("encoding"),
        )


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    """Egress policy for the agent's environment.

    The allowlist is rendered as the API expects — a tuple of small dicts such as
    ``[{"domain": "*"}]``. Use :meth:`allow_domains` rather than building the
    dicts by hand so the wire shape stays in one place.
    """

    allowlist: tuple[dict, ...] = ()

    def to_api(self) -> dict:
        return {"allowlist": [dict(entry) for entry in self.allowlist]}

    @classmethod
    def allow_domains(cls, *domains: str) -> "NetworkConfig":
        """Build a config allowing egress to the given domains.

        Pass ``"*"`` to allow all egress (``allow_domains("*")``).
        """
        return cls(allowlist=tuple({"domain": d} for d in domains))

    @classmethod
    def from_api(cls, d: dict) -> "NetworkConfig":
        return cls(allowlist=tuple(d.get("allowlist", []) or ()))


@dataclass(frozen=True, slots=True)
class BaseEnvironment:
    """The sandbox an agent runs in: mounted sources plus egress policy.

    ``type`` is ``"remote"`` for the managed (cloud-executed) environment, which
    is the only one this control plane provisions.
    """

    sources: tuple[EnvironmentSource, ...] = ()
    network: NetworkConfig = field(default_factory=NetworkConfig)
    type: str = "remote"

    def to_api(self) -> dict:
        return {
            "type": self.type,
            "sources": [s.to_api() for s in self.sources],
            "network": self.network.to_api(),
        }

    @classmethod
    def from_api(cls, d: dict) -> "BaseEnvironment":
        return cls(
            type=d.get("type", "remote"),
            sources=tuple(
                EnvironmentSource.from_api(s) for s in d.get("sources", []) or ()
            ),
            network=NetworkConfig.from_api(d.get("network", {}) or {}),
        )


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A tool the agent may call.

    Built-in tools (``code_execution``, ``google_search``, ``url_context``,
    ``filesystem``) are pure ``type`` markers. An MCP server tool additionally
    carries a ``name``, ``url``, and optional ``headers``. The classmethods are
    the supported constructors — prefer them over the bare initializer so the
    valid combinations stay encoded in one place.
    """

    type: str
    name: str | None = None
    url: str | None = None
    headers: dict | None = None

    def to_api(self) -> dict:
        body: dict = {"type": self.type}
        if self.name is not None:
            body["name"] = self.name
        if self.url is not None:
            body["url"] = self.url
        if self.headers is not None:
            body["headers"] = dict(self.headers)
        return body

    # -- supported constructors -------------------------------------------

    @classmethod
    def code_execution(cls) -> "AgentTool":
        return cls(type="code_execution")

    @classmethod
    def google_search(cls) -> "AgentTool":
        return cls(type="google_search")

    @classmethod
    def url_context(cls) -> "AgentTool":
        return cls(type="url_context")

    @classmethod
    def filesystem(cls) -> "AgentTool":
        return cls(type="filesystem")

    @classmethod
    def mcp_server(
        cls, name: str, url: str, headers: dict | None = None
    ) -> "AgentTool":
        return cls(type="mcp_server", name=name, url=url, headers=headers)

    @classmethod
    def from_api(cls, d: dict) -> "AgentTool":
        return cls(
            type=d.get("type", ""),
            name=d.get("name"),
            url=d.get("url"),
            headers=d.get("headers"),
        )


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """A declarative description of a managed agent.

    ``id`` doubles as both the resource id (``agent_id`` query param on create)
    and the ``name`` in the body, matching the platform sample. ``base_agent``
    selects the foundation agent the definition extends.
    """

    id: str
    base_agent: str = "antigravity-preview-05-2026"
    description: str = ""
    system_instruction: str = ""
    tools: tuple[AgentTool, ...] = ()
    base_environment: BaseEnvironment | None = None

    def to_api(self) -> dict:
        body: dict = {
            "name": self.id,
            "base_agent": self.base_agent,
            "description": self.description,
            "system_instruction": self.system_instruction,
            "tools": [t.to_api() for t in self.tools],
        }
        if self.base_environment is not None:
            body["base_environment"] = self.base_environment.to_api()
        return body

    @classmethod
    def from_api(cls, d: dict) -> "AgentSpec":
        env = d.get("base_environment")
        # The resource name comes back fully qualified; the id is the last seg.
        raw_name = d.get("name", "")
        agent_id = raw_name.rsplit("/", 1)[-1] if raw_name else ""
        return cls(
            id=agent_id,
            base_agent=d.get("base_agent", "antigravity-preview-05-2026"),
            description=d.get("description", ""),
            system_instruction=d.get("system_instruction", ""),
            tools=tuple(AgentTool.from_api(t) for t in d.get("tools", []) or ()),
            base_environment=BaseEnvironment.from_api(env) if env else None,
        )


@dataclass(frozen=True, slots=True)
class InteractionEvent:
    """A single event from an interaction stream.

    The Interactions API streams heterogeneous JSON dicts (text deltas, tool
    calls, status updates). Rather than model every variant, this wraps the raw
    dict and exposes the two accessors callers reach for most: :attr:`type` and
    :attr:`text`. The full payload is always available via :attr:`raw`.
    """

    raw: dict

    @property
    def type(self) -> str:
        return str(self.raw.get("type", "") or self.raw.get("event_type", ""))

    @property
    def text(self) -> str:
        """Best-effort text extraction across the shapes events arrive in."""
        if "text" in self.raw:
            return str(self.raw["text"])
        # Some events nest text under content/{parts|text}.
        content = self.raw.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if "text" in content:
                return str(content["text"])
            parts = content.get("parts")
            if isinstance(parts, list):
                return "".join(
                    str(p.get("text", ""))
                    for p in parts
                    if isinstance(p, dict)
                )
        return ""

    @classmethod
    def from_api(cls, d: dict) -> "InteractionEvent":
        return cls(raw=d)
