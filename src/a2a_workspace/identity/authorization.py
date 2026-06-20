"""The request-scoped authorization bundle that keeps the two planes separate.

A :class:`RequestContext` carries:

* ``principal`` — the verified identity (agentAuthorization plane). Isolation
  decisions are made *only* from this.
* ``tool_credential`` — an opaque, short-lived credential for reaching the
  user's storage (toolAuthorization plane). It is handed to the trusted
  ``StorageAdapter`` and to nothing else.

The ``ToolCredential`` is intentionally an opaque holder, not a string the rest
of the code can read. Its ``__repr__`` redacts the secret, and a module-level
contract (enforced by tests) is that it must never be placed into a
``LocalConnectionStrategy`` or any structure visible to the model/Antigravity
runtime. Tokens that the LLM can see are tokens the LLM can exfiltrate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from a2a_workspace.identity.principal import Principal


class CredentialKind(str, Enum):
    """How the tool credential was obtained.

    DELEGATED_USER_OAUTH is the strongest default: storage evaluates the end
    user's own IAM. DOWNSCOPED_BROKER is a credential minted by the broker and
    restricted (via a Credential Access Boundary) to one workspace prefix.
    """

    DELEGATED_USER_OAUTH = "delegated_user_oauth"
    DOWNSCOPED_BROKER = "downscoped_broker"


@dataclass(frozen=True, slots=True)
class ToolCredential:
    """An opaque, workspace-scoped storage credential. Never shown to the model.

    ``secret`` is the actual bearer value handed to the storage client. It is
    excluded from ``repr``/equality so it does not leak into logs, tracebacks, or
    accidental serialization.
    """

    kind: CredentialKind
    secret: str = field(repr=False, compare=False)
    # The single workspace prefix this credential is permitted to touch. For a
    # downscoped credential this mirrors the Credential Access Boundary; for a
    # delegated credential it documents the *expected* reach (storage IAM is the
    # real enforcer). The adapter asserts every access stays within it.
    scope_prefix: str | None = None
    expires_at_epoch: float | None = None

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"ToolCredential(kind={self.kind.value}, "
            f"scope_prefix={self.scope_prefix!r}, secret=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Everything a request needs, with the two identity planes kept distinct."""

    principal: Principal
    tool_credential: ToolCredential | None = None
    # Free-form, non-authoritative metadata (request id, client info). Never used
    # for isolation; present for audit correlation only.
    attributes: dict[str, str] = field(default_factory=dict)

    def require_tool_credential(self) -> ToolCredential:
        if self.tool_credential is None:
            raise PermissionError(
                "operation requires a tool-authorization credential but none was "
                "supplied on the request"
            )
        return self.tool_credential
