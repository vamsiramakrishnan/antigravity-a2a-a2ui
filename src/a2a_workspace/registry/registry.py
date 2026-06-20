"""The WorkspaceRegistry port.

Kept narrow on purpose. Note what is *absent*: there is no "list all workspaces"
or "read arbitrary workspace" method exposed to the gateway. The gateway resolves
a workspace only via the principal it has already verified.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from a2a_workspace.identity.principal import Principal
from a2a_workspace.registry.models import Generation, Revision, Workspace


@runtime_checkable
class WorkspaceRegistry(Protocol):
    def ensure_workspace(
        self, principal: Principal, *, organization: str, environment: str, region: str
    ) -> Workspace:
        """Idempotently return (creating if needed) the principal's workspace."""
        ...

    def get_workspace(self, workspace_id: str) -> Workspace:
        """Return a workspace by id, or raise ``NotFoundError``."""
        ...

    def workspace_owner(self, workspace_id: str) -> str | None:
        """Return the owning principal key, or ``None`` if unknown.

        Used by the broker to enforce the principal->workspace binding before
        minting a credential.
        """
        ...

    def add_revision(self, revision: Revision) -> Revision: ...

    def get_revision(self, workspace_id: str, content_digest: str) -> Revision: ...

    def activate_revision(
        self, workspace_id: str, content_digest: str, *, note: str = ""
    ) -> Generation:
        """Append a new active generation pointing at ``content_digest``."""
        ...

    def resolve_active_generation(self, workspace_id: str) -> Generation:
        """Return the current active generation, or raise ``NotFoundError``."""
        ...

    def get_generation(self, workspace_id: str, number: int) -> Generation: ...
