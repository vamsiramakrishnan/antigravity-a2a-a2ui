"""The StorageAdapter port: the only thing that talks to object storage.

An adapter is constructed *per request* and bound to one workspace and one
``ToolCredential``. That binding is the point: there is no long-lived,
omnipotent storage client in the process. Two invariants every implementation
must uphold:

1. Refuse any key that ``WorkspaceLayout.contains`` rejects (path-traversal /
   cross-tenant guard).
2. If the credential carries a ``scope_prefix``, refuse any key outside it. This
   makes the application layer agree with the Credential Access Boundary; a
   mismatch is a bug worth failing loudly on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from a2a_workspace.errors import IsolationError
from a2a_workspace.identity.authorization import ToolCredential
from a2a_workspace.storage.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class ObjectRef:
    key: str
    size: int
    etag: str | None = None


@runtime_checkable
class StorageAdapter(Protocol):
    """Per-request, workspace-scoped object access."""

    @property
    def layout(self) -> WorkspaceLayout: ...

    def get_object(self, key: str) -> bytes: ...

    def put_object(self, key: str, data: bytes) -> ObjectRef: ...

    def list_objects(self, prefix: str) -> list[ObjectRef]: ...

    def delete_prefix(self, prefix: str) -> int: ...


class GuardedStorageAdapter:
    """Mixin enforcing the two isolation checks for concrete adapters.

    Concrete adapters call :meth:`_guard` on every key before touching the
    backend, so the boundary lives in exactly one place.
    """

    def __init__(
        self, *, layout: WorkspaceLayout, credential: ToolCredential
    ) -> None:
        self._layout = layout
        self._credential = credential

    @property
    def layout(self) -> WorkspaceLayout:
        return self._layout

    def _guard(self, key: str) -> str:
        if not self._layout.contains(key):
            raise IsolationError(
                f"key {key!r} is outside workspace {self._layout.workspace_id}"
            )
        scope = self._credential.scope_prefix
        if scope is not None and not key.startswith(scope):
            raise IsolationError(
                f"key {key!r} is outside the credential scope {scope!r}"
            )
        return key
