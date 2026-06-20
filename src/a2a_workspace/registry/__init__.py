"""The workspace registry: source of truth for identity->workspace and which
revision is active.

The registry holds *metadata only* — it never stores skill bytes (those live as
immutable revisions in object storage). It maps a principal to a workspace, keeps
the catalogue of revisions, and tracks the active "generation" pointer.

It also owns the *bounded* publication tools (:mod:`a2a_workspace.registry.drafts`).
This is deliberate: the model never writes to object storage directly. It can
only call ``create_draft`` / ``apply_patch`` / ``validate`` / ``submit`` / and
the registry performs the actual immutable publish.
"""

from a2a_workspace.registry.drafts import DraftService
from a2a_workspace.registry.memory import InMemoryRegistry
from a2a_workspace.registry.models import (
    Draft,
    Generation,
    Revision,
    Workspace,
)
from a2a_workspace.registry.registry import WorkspaceRegistry

__all__ = [
    "Draft",
    "DraftService",
    "Generation",
    "InMemoryRegistry",
    "Revision",
    "Workspace",
    "WorkspaceRegistry",
]
