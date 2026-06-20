"""The two identity planes.

Gemini Enterprise separates ``agentAuthorization`` (who may invoke the agent)
from ``toolAuthorizations`` (credentials the agent may use to reach downstream
resources on the user's behalf). This package keeps those planes apart in code
so they cannot be accidentally conflated:

* :mod:`a2a_workspace.identity.principal` — the immutable, verified identity we
  isolate on.
* :mod:`a2a_workspace.identity.verifier` — turns an inbound bearer token into a
  ``Principal`` (or refuses).
* :mod:`a2a_workspace.identity.authorization` — the request-scoped bundle that
  carries the principal plus an *opaque* tool credential. The tool credential is
  deliberately not a ``Principal`` and is never used for isolation decisions.
"""

from a2a_workspace.identity.authorization import (
    RequestContext,
    ToolCredential,
)
from a2a_workspace.identity.principal import Principal
from a2a_workspace.identity.verifier import (
    DevIdentityVerifier,
    IdentityVerifier,
    JwtIdentityVerifier,
)

__all__ = [
    "DevIdentityVerifier",
    "IdentityVerifier",
    "JwtIdentityVerifier",
    "Principal",
    "RequestContext",
    "ToolCredential",
]
