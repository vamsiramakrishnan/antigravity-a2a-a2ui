"""The object-key layout for a workspace.

Every key a workspace can touch lives under ``workspaces/{workspace_id}/``. The
``workspace_id`` is a random UUID, never an email, so object names leak nothing
about the user. Revisions are content-addressed and immutable:

    workspaces/{workspace_id}/
    ├── metadata/workspace.json
    ├── drafts/{draft_id}/...                # mutable scratch space
    ├── revisions/sha256-{digest}/           # immutable, content-addressed
    │   ├── manifest.json
    │   └── skills/...
    ├── activations/{generation}.json        # which revision is "active"
    └── exports/

The single most important method here is :meth:`WorkspaceLayout.contains`: it is
the application-level half of the isolation boundary (storage IAM on the managed
folder is the other half). The adapter calls it before every operation.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKSPACE_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


@dataclass(frozen=True, slots=True)
class WorkspaceLayout:
    """Computes (and guards) object keys for a single workspace."""

    workspace_id: str

    def __post_init__(self) -> None:
        if not _WORKSPACE_ID_RE.match(self.workspace_id):
            raise ValueError(f"workspace_id must be a UUID, got {self.workspace_id!r}")

    @property
    def prefix(self) -> str:
        return f"workspaces/{self.workspace_id}/"

    def metadata_key(self) -> str:
        return self._join("metadata/workspace.json")

    def draft_prefix(self, draft_id: str) -> str:
        return self._join(f"drafts/{_safe(draft_id)}/")

    def revision_prefix(self, digest: str) -> str:
        if not _DIGEST_RE.match(digest):
            raise ValueError(f"revision digest must be 64 hex chars, got {digest!r}")
        return self._join(f"revisions/sha256-{digest}/")

    def revision_manifest_key(self, digest: str) -> str:
        return self.revision_prefix(digest) + "manifest.json"

    def revision_skills_prefix(self, digest: str) -> str:
        return self.revision_prefix(digest) + "skills/"

    def activation_key(self, generation: int) -> str:
        return self._join(f"activations/{int(generation)}.json")

    def contains(self, key: str) -> bool:
        """True iff ``key`` resolves to somewhere inside this workspace.

        Normalises the path first so ``..`` traversal cannot escape the prefix.
        This is the chokepoint that turns a path-traversal bug into a refusal
        rather than a cross-tenant read.
        """
        normalized = posixpath.normpath("/" + key).lstrip("/")
        # normpath strips trailing slashes; compare against the prefix without it.
        return normalized == self.prefix.rstrip("/") or normalized.startswith(
            self.prefix
        )

    def _join(self, suffix: str) -> str:
        key = self.prefix + suffix
        if not self.contains(key):
            # Defensive: a malformed suffix (embedded '..') must never produce a
            # key outside the prefix.
            raise ValueError(f"computed key {key!r} escapes workspace prefix")
        return key


def _safe(segment: str) -> str:
    """Reject path segments that could be used to climb out of the prefix."""
    if not segment or "/" in segment or segment in (".", "..") or "\\" in segment:
        raise ValueError(f"unsafe path segment: {segment!r}")
    return segment
