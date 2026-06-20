"""Trusted storage layer.

Nothing above this layer ever sees a raw storage credential or a bucket name.
Callers hand the :class:`~a2a_workspace.storage.adapter.StorageAdapter` a
:class:`~a2a_workspace.identity.authorization.ToolCredential` and a workspace id;
the adapter resolves the object layout, asserts every access stays inside the
workspace prefix, and talks to the backend.
"""

from a2a_workspace.storage.adapter import (
    ObjectRef,
    StorageAdapter,
)
from a2a_workspace.storage.layout import WorkspaceLayout
from a2a_workspace.storage.local import LocalStorageAdapter

__all__ = [
    "LocalStorageAdapter",
    "ObjectRef",
    "StorageAdapter",
    "WorkspaceLayout",
]
