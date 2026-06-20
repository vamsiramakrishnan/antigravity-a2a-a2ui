"""Registry domain models.

A :class:`Workspace` belongs to exactly one principal (by ``principal_key`` —
never email). It owns a set of immutable :class:`Revision` objects and an ordered
list of :class:`Generation` pointers. The *active* generation names the revision
new conversations get; bumping it never disturbs conversations already pinned to
an older generation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class DraftStatus(str, Enum):
    OPEN = "open"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    PUBLISHED = "published"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class Revision:
    """An immutable, content-addressed skill bundle.

    ``content_digest`` is the Merkle-style digest of the whole skills tree (see
    :mod:`a2a_workspace.materializer.integrity`). It is both the storage path
    component (``revisions/sha256-{digest}/``) and the value the materializer
    re-verifies after download. Identity == content, so two identical bundles
    collapse to one revision.
    """

    workspace_id: str
    content_digest: str
    manifest: dict
    created_at_epoch: float = field(default_factory=time.time)
    parent_digest: str | None = None


@dataclass(frozen=True, slots=True)
class Generation:
    """A numbered pointer to a revision. The active generation is the latest one.

    Generations are append-only and monotonic. A conversation records the
    generation it started on and stays there for life.
    """

    workspace_id: str
    number: int
    content_digest: str
    activated_at_epoch: float = field(default_factory=time.time)
    note: str = ""


@dataclass(slots=True)
class Workspace:
    """Metadata for one user's workspace. Bytes live in storage, not here."""

    workspace_id: str
    principal_key: str
    organization: str
    environment: str
    region: str
    created_at_epoch: float = field(default_factory=time.time)
    display_email: str | None = None  # display metadata only
    active_generation: int | None = None


@dataclass(slots=True)
class Draft:
    """Mutable scratch space for assembling the next revision.

    A draft is a flat map of relative path -> bytes. It is the only mutable skill
    surface; the model edits a draft through bounded tools and never the active
    revision. ``base_digest`` records which revision it was forked from for a
    clean three-way story if needed.
    """

    draft_id: str
    workspace_id: str
    status: DraftStatus = DraftStatus.OPEN
    base_digest: str | None = None
    files: dict[str, bytes] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    created_at_epoch: float = field(default_factory=time.time)
