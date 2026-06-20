"""The identity we isolate on.

A :class:`Principal` is the verified ``(issuer, subject)`` tuple lifted out of a
validated OAuth/OIDC token. It is the *only* thing the system uses to decide
which workspace a request may touch.

Why not email? Email is mutable, reassignable, and frequently arrives as a
display string inside a prompt or an A2A message body — i.e. attacker-influenced
data. ``iss``/``sub`` come from a signature-verified token and are stable for the
life of the account. Email is retained here purely as display metadata and is
never consulted for an authorization or isolation decision.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field

# RFC 8414 issuers are URLs; subjects are opaque. We keep validation loose but
# non-empty, and we normalise so that the same identity always hashes the same.
_WHITESPACE = re.compile(r"\s")


@dataclass(frozen=True, slots=True)
class Principal:
    """An immutable, verified caller identity.

    Instances are frozen and hashable so they can be used as map keys and so a
    principal cannot be mutated after verification.
    """

    issuer: str
    subject: str
    email: str | None = field(default=None, compare=False)
    display_name: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.issuer or _WHITESPACE.search(self.issuer):
            raise ValueError("issuer must be a non-empty token without whitespace")
        if not self.subject or _WHITESPACE.search(self.subject):
            raise ValueError("subject must be a non-empty token without whitespace")

    @property
    def key(self) -> str:
        """A stable, opaque key for this identity.

        Used to look up the workspace mapping. We hash rather than concatenate so
        the key has fixed shape and does not leak the raw issuer/subject into
        logs or object names.
        """
        digest = hashlib.sha256(f"{self.issuer}\x00{self.subject}".encode()).hexdigest()
        return f"prn_{digest[:32]}"

    def derive_workspace_id(self, *, namespace: uuid.UUID) -> str:
        """Deterministically derive a workspace UUID for this principal.

        Deterministic (UUIDv5) so that ``ensure_workspace`` is idempotent without
        a round-trip: the same principal always maps to the same workspace id,
        but the id is a UUID rather than anything that reveals the identity. The
        registry remains the source of truth; this only seeds first creation.
        """
        return str(uuid.uuid5(namespace, self.key))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"Principal(issuer={self.issuer!r}, subject=<redacted>, key={self.key})"
