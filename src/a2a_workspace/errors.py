"""Exception hierarchy for the workspace control plane.

These are intentionally coarse. The HTTP layer maps them to status codes; the
rest of the code uses them to distinguish *expected* failures (a workspace that
does not exist yet) from *security-relevant* failures (a principal reaching for
a workspace that is not theirs, or a revision whose bytes do not match their
advertised digest). The latter should always be logged and never swallowed.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base class for all control-plane errors."""


class NotFoundError(WorkspaceError):
    """A requested resource (workspace, revision, generation) does not exist."""


class ConflictError(WorkspaceError):
    """An optimistic-concurrency precondition failed (e.g. ETag mismatch)."""


class IsolationError(WorkspaceError):
    """A principal attempted to reach a resource outside its own tenant boundary.

    Raising this is a security event: it means application-level checks caught
    something that storage-level IAM should also have refused. Both layers
    exist on purpose (defense in depth); a hit here is worth alerting on.
    """


class IntegrityError(WorkspaceError):
    """Materialized bytes did not match the expected content digest.

    The only safe response is to discard the materialized tree and refuse to
    start the session. A digest mismatch means either corruption or tampering;
    neither is acceptable for code that is about to be executed as a skill.
    """


class AuthorizationError(WorkspaceError):
    """The request could not be authenticated or carried insufficient scope."""


class ValidationError(WorkspaceError):
    """A draft or revision failed structural/policy validation before publish."""
