"""Advanced agent config: spec + two outputs.

:class:`AdvancedAgentConfigSpec` is the serializable description of a fully
hardened agent — skills, data dir, tools, MCP HTTP servers, plus the policy /
hook / trigger / capabilities specs from :mod:`hardening`.

Two outputs:

* :meth:`AdvancedAgentConfigSpec.to_remote_agent_tools` — pure; emits the tool
  entries (``code_execution`` / ``filesystem`` / ``mcp_server``) for the remote
  Agents API request body. Testable without the SDK.
* :func:`build_local_agent_config` — import-guarded; assembles a real
  ``google.antigravity.LocalAgentConfig`` wiring policies, hooks, triggers,
  capabilities, workspaces, and MCP HTTP servers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from a2a_workspace.antigravity.hardening import (
    CapabilitiesSpec,
    HookSpec,
    PolicySpec,
    TriggerSpec,
)

# (name, url, headers) for a streamable-HTTP MCP server.
McpHttpServer = tuple[str, str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class AdvancedAgentConfigSpec:
    """Serializable description of a hardened Antigravity agent."""

    skills_paths: tuple[str, ...] = ()
    app_data_dir: str = ""
    work_dir: str = ""
    tools: tuple[Callable, ...] = ()
    mcp_http_servers: tuple[McpHttpServer, ...] = ()
    policy_specs: tuple[PolicySpec, ...] = ()
    hook_specs: tuple[HookSpec, ...] = ()
    trigger_specs: tuple[TriggerSpec, ...] = ()
    capabilities_spec: CapabilitiesSpec = field(default_factory=CapabilitiesSpec)
    system_instructions: str = ""
    model: str | None = None

    def to_remote_agent_tools(self) -> list[dict]:
        """Describe the remote agent's tool entries for the Agents API body.

        Pure and deterministic. Emits, in order:

        * a ``filesystem`` tool scoped to ``work_dir`` (when set);
        * a ``code_execution`` tool *only* when ``RUN_COMMAND`` is not in the
          capabilities' ``disabled_tools`` (i.e. the agent may shell out);
        * one ``mcp_server`` entry per HTTP MCP server.
        """
        tools: list[dict] = []
        if self.work_dir:
            tools.append(
                {
                    "type": "filesystem",
                    "filesystem": {"root": self.work_dir, "read_only": False},
                }
            )
        if "RUN_COMMAND" not in self.capabilities_spec.disabled_tools:
            tools.append({"type": "code_execution", "code_execution": {}})
        for name, url, headers in self.mcp_http_servers:
            tools.append(
                {
                    "type": "mcp_server",
                    "mcp_server": {
                        "name": name,
                        "url": url,
                        "headers": dict(headers),
                    },
                }
            )
        return tools


def build_local_agent_config(
    spec: AdvancedAgentConfigSpec,
    *,
    emit: Callable[[dict], None],
    redact: Callable[[str], str],
    trigger_handlers: dict[str, Callable],
):
    """Assemble a real ``LocalAgentConfig`` from ``spec``.

    Wires policies (with workspace confinement + exfil DENY), audit + redaction
    hooks, triggers, capabilities, workspaces, and streamable-HTTP MCP servers.
    Raises ``ImportError`` (with install guidance) if the SDK is absent.
    """
    try:
        from google.antigravity import LocalAgentConfig  # type: ignore
        from google.antigravity.types import (  # type: ignore
            CapabilitiesConfig,
            McpStreamableHttpServer,
        )
    except ImportError as exc:  # pragma: no cover - optional SDK
        raise ImportError(
            "build_local_agent_config requires the Antigravity SDK: pip install "
            "google-antigravity"
        ) from exc

    from a2a_workspace.antigravity.hooks import audit_hooks, redaction_hook
    from a2a_workspace.antigravity.policies import build_policies
    from a2a_workspace.antigravity.triggers import build_triggers

    policies = build_policies(
        list(spec.policy_specs), work_dir=spec.work_dir
    )

    hooks: list = []
    hooks.extend(audit_hooks(emit))
    hooks.append(redaction_hook(redact))

    triggers = build_triggers(list(spec.trigger_specs), trigger_handlers)

    cap = spec.capabilities_spec
    capabilities = CapabilitiesConfig(
        enable_subagents=cap.enable_subagents,
        enabled_tools=list(cap.enabled_tools) if cap.enabled_tools else None,
        disabled_tools=list(cap.disabled_tools),
        compaction_threshold=cap.compaction_threshold,
    )

    mcp_servers = [
        McpStreamableHttpServer(name=name, url=url, headers=dict(headers))
        for name, url, headers in spec.mcp_http_servers
    ]

    return LocalAgentConfig(
        system_instructions=spec.system_instructions,
        capabilities=capabilities,
        tools=list(spec.tools),
        policies=policies,
        hooks=hooks,
        triggers=triggers,
        mcp_servers=mcp_servers,
        workspaces=[spec.work_dir] if spec.work_dir else [],
        skills_paths=list(spec.skills_paths),
        app_data_dir=spec.app_data_dir,
        model=spec.model,
    )
