"""Secure multi-tenant A2A/A2UI workspace gateway for Antigravity skills.

This package implements the control-plane architecture for serving per-user
Antigravity skill workspaces from a *shared, stateless* Cloud Run service
without granting that service broad access to user data.

The design rests on a few load-bearing rules; see ``docs/architecture.md`` for
the full rationale. The most important ones are encoded directly in the types:

* Tenant isolation is derived from a verified OAuth ``(issuer, subject)`` tuple
  (:class:`~a2a_workspace.identity.principal.Principal`), never from an email
  or any value that originates in a prompt or A2A message body.
* The gateway holds no broad object credential. Storage is reached only through
  a :class:`~a2a_workspace.storage.adapter.StorageAdapter` that is handed a
  *per-request*, *workspace-scoped* credential (delegated user OAuth, or a
  downscoped credential minted by the broker).
* Skill revisions are immutable and content-addressed; a conversation is pinned
  to one generation for its whole life.
"""

from a2a_workspace.errors import (
    IntegrityError,
    IsolationError,
    NotFoundError,
    WorkspaceError,
)

__all__ = [
    "IntegrityError",
    "IsolationError",
    "NotFoundError",
    "WorkspaceError",
    "__version__",
]

__version__ = "0.1.0"
