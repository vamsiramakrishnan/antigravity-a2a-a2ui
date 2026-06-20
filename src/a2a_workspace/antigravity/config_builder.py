"""Build the Antigravity SDK's LocalAgentConfig from a materialized session.

Two layers, mirroring the rest of the project:

* :class:`AgentConfigSpec` — a plain, serializable description of the agent
  config (read-only skills paths, app data dir, tool callables, MCP servers).
  Always available; used by tests and by the file-browser surface.
* :func:`build_local_agent_config` — converts that spec into a real
  ``google.antigravity.LocalAgentConfig``. Import-guarded: it raises a clear
  error if the SDK is not installed rather than degrading silently.

The session file (:func:`write_session_file`) is how the agent's proxy tools get
their short-lived session token without it ever appearing in the model-visible
connection ``env``: the control plane writes it under ``app_data_dir/.a2a/`` and
the generated ``enterprise_tools.py`` reads it at call time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """A stdio MCP server to expose as a tool source (e.g. a custom connector)."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentConfigSpec:
    skills_paths: tuple[str, ...]
    app_data_dir: str
    tools: tuple[Callable, ...] = ()
    mcp_servers: tuple[McpServerSpec, ...] = ()
    read_only_skills: bool = True


def write_session_file(
    *,
    app_data_dir: Path | str,
    gateway_url: str,
    session_token: str,
    conversation_id: str,
    inputs_dir: Path | str | None = None,
    work_dir: Path | str | None = None,
) -> Path:
    """Drop the session file the proxy tools read. Returns its path.

    Kept out of the connection ``env`` on purpose: the token rides in a file under
    the agent's data dir, scoped and short-lived, never in the model-visible
    connection configuration. ``inputs_dir``/``work_dir`` let the agent's tools
    locate uploaded files and a place to write fetched artifacts.
    """
    target = Path(app_data_dir) / ".a2a" / "session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gateway_url": gateway_url,
        "session_token": session_token,
        "conversation_id": conversation_id,
    }
    if inputs_dir is not None:
        payload["inputs_dir"] = str(inputs_dir)
    if work_dir is not None:
        payload["work_dir"] = str(work_dir)
    target.write_text(json.dumps(payload))
    return target


def build_local_agent_config(spec: AgentConfigSpec):
    """Construct a real ``google.antigravity.LocalAgentConfig`` from ``spec``.

    Raises ``ImportError`` (with install guidance) if the Antigravity SDK is not
    present. Network/runtime use lives here; the rest of the system depends only
    on :class:`AgentConfigSpec`.
    """
    try:
        from google.antigravity import LocalAgentConfig  # type: ignore
        from google.antigravity.types import McpStdioServer  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional SDK
        raise ImportError(
            "build_local_agent_config requires the Antigravity SDK: pip install "
            "google-antigravity"
        ) from exc

    mcp_servers = [
        McpStdioServer(
            name=s.name, command=s.command, args=list(s.args), env=dict(s.env)
        )
        for s in spec.mcp_servers
    ]
    return LocalAgentConfig(
        tools=list(spec.tools),
        mcp_servers=mcp_servers,
        skills_paths=list(spec.skills_paths),
        app_data_dir=spec.app_data_dir,
    )
