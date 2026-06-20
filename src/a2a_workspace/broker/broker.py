"""Credential broker implementations.

A :class:`CredentialBroker` turns "this verified principal wants to act on this
workspace" into a :class:`ToolCredential` scoped to exactly that workspace's
prefix. Two strategies match the two deployment modes in the architecture:

* :class:`DelegatedOAuthBroker` — the request already carries a delegated
  end-user OAuth token (the strong default). The broker simply tags it with the
  workspace scope; storage IAM does the real enforcement.

* :class:`DownscopedCredentialBroker` — no end-user token is available, so the
  broker mints a credential from a privileged service identity and *downscopes*
  it with a Credential Access Boundary to the workspace prefix and a minimal
  permission set. This is where the privilege lives; it must be deployed apart
  from the gateway and never wired to the model.

The broker is the place that enforces the principal->workspace binding before
any credential is produced, so a caller cannot ask for a workspace that is not
theirs even if the rest of the system had a bug.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from a2a_workspace.errors import AuthorizationError, IsolationError
from a2a_workspace.identity.authorization import CredentialKind, ToolCredential
from a2a_workspace.identity.principal import Principal
from a2a_workspace.storage.layout import WorkspaceLayout


@runtime_checkable
class CredentialBroker(Protocol):
    def issue(
        self,
        *,
        principal: Principal,
        workspace_id: str,
        permissions: tuple[str, ...] = ("storage.objects.get", "storage.objects.list"),
    ) -> ToolCredential: ...


class DelegatedOAuthBroker:
    """Wraps a delegated end-user OAuth token as a workspace-scoped credential.

    The token itself is supplied per request (toolAuthorization plane). The
    broker's job is just to bind it to the workspace prefix so the adapter's
    scope check has something to compare against; storage IAM remains the
    authority on what the user may actually read.
    """

    def __init__(self, *, token_provider) -> None:
        # token_provider(principal, workspace_id) -> (token, expiry_epoch|None)
        self._token_provider = token_provider

    def issue(
        self,
        *,
        principal: Principal,
        workspace_id: str,
        permissions: tuple[str, ...] = ("storage.objects.get", "storage.objects.list"),
    ) -> ToolCredential:
        layout = WorkspaceLayout(workspace_id)
        token, expiry = self._token_provider(principal, workspace_id)
        if not token:
            raise AuthorizationError(
                "no delegated user-OAuth token available for this request"
            )
        return ToolCredential(
            kind=CredentialKind.DELEGATED_USER_OAUTH,
            secret=token,
            scope_prefix=layout.prefix,
            expires_at_epoch=expiry,
        )


class DownscopedCredentialBroker:
    """Mints a short-lived credential restricted to one workspace prefix.

    In production ``_minter`` calls the STS downscoping flow (Credential Access
    Boundaries) against the broker's privileged service account. Here it is
    injectable so the policy construction and the principal->workspace check can
    be tested without cloud calls.
    """

    def __init__(
        self,
        *,
        bucket: str,
        registry,
        minter,
        ttl_seconds: int = 600,
    ) -> None:
        self._bucket = bucket
        self._registry = registry
        self._minter = minter  # (boundary: dict, ttl: int) -> token: str
        self._ttl = ttl_seconds

    def issue(
        self,
        *,
        principal: Principal,
        workspace_id: str,
        permissions: tuple[str, ...] = ("storage.objects.get", "storage.objects.list"),
    ) -> ToolCredential:
        # Enforce the binding here, before any credential exists. The registry is
        # the source of truth for which workspace a principal owns.
        owner = self._registry.workspace_owner(workspace_id)
        if owner is None:
            raise AuthorizationError(f"unknown workspace {workspace_id}")
        if owner != principal.key:
            raise IsolationError(
                "principal does not own the requested workspace; refusing to "
                "mint a credential"
            )

        layout = WorkspaceLayout(workspace_id)
        boundary = self._build_boundary(layout.prefix, permissions)
        token = self._minter(boundary, self._ttl)
        return ToolCredential(
            kind=CredentialKind.DOWNSCOPED_BROKER,
            secret=token,
            scope_prefix=layout.prefix,
            expires_at_epoch=time.time() + self._ttl,
        )

    def _build_boundary(
        self, prefix: str, permissions: tuple[str, ...]
    ) -> dict:
        """Construct a Credential Access Boundary restricting access to ``prefix``.

        The ``resource.name.startsWith`` availability condition is what makes the
        downscoped token unable to touch any other workspace, even though it is
        derived from a service account that can see the whole bucket.
        """
        return {
            "accessBoundary": {
                "accessBoundaryRules": [
                    {
                        "availableResource": (
                            f"//storage.googleapis.com/projects/_/buckets/{self._bucket}"
                        ),
                        "availablePermissions": [
                            f"inRole:roles/storage.objectViewer"
                            if perms_are_read_only(permissions)
                            else "inRole:roles/storage.objectAdmin"
                        ],
                        "availabilityCondition": {
                            "title": f"scope-to-{prefix}",
                            "expression": (
                                "resource.name.startsWith("
                                f"'projects/_/buckets/{self._bucket}/objects/{prefix}')"
                            ),
                        },
                    }
                ]
            }
        }


def perms_are_read_only(permissions: tuple[str, ...]) -> bool:
    return all(p.endswith((".get", ".list")) for p in permissions)
