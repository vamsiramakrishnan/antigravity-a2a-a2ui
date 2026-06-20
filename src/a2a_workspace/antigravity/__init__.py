"""Antigravity SDK wiring.

Turns a materialized session plus a set of proxy tools into the configuration the
Antigravity SDK consumes (``LocalAgentConfig`` with ``tools`` / ``mcp_servers`` /
``skills_paths`` / ``app_data_dir``). The real SDK import is guarded so the
control plane builds and tests without ``google-antigravity`` installed.
"""

from a2a_workspace.antigravity.config_builder import (
    AgentConfigSpec,
    McpServerSpec,
    build_local_agent_config,
    write_session_file,
)

__all__ = [
    "AgentConfigSpec",
    "McpServerSpec",
    "build_local_agent_config",
    "write_session_file",
]
