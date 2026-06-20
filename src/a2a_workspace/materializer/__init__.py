"""Session materialization: turn an immutable revision into a verified local tree.

Antigravity consumes a *local* materialization, never a live writable bucket
mount. This package downloads a revision through the trusted storage adapter and
re-verifies its content digest before any skill is allowed to run.
"""

from a2a_workspace.materializer.integrity import (
    content_digest_for_tree,
    digest_files,
    verify_tree,
)
from a2a_workspace.materializer.materializer import (
    MaterializedSession,
    SessionMaterializer,
)

__all__ = [
    "MaterializedSession",
    "SessionMaterializer",
    "content_digest_for_tree",
    "digest_files",
    "verify_tree",
]
