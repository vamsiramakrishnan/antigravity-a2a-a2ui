"""The credential broker: mints short-lived, workspace-scoped storage credentials.

This is the privileged component in the "downscoped" deployment mode. It — and
only it — can vend access to a workspace's storage prefix. It is deliberately
separate from the gateway and from Antigravity: it is never exposed as an agent
tool, it validates the principal -> workspace mapping itself, and every issuance
is auditable.
"""

from a2a_workspace.broker.broker import (
    CredentialBroker,
    DelegatedOAuthBroker,
    DownscopedCredentialBroker,
)

__all__ = [
    "CredentialBroker",
    "DelegatedOAuthBroker",
    "DownscopedCredentialBroker",
]
