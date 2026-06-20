"""Request-scoped dependencies for the gateway.

The key one is :func:`request_context`: it turns the two inbound headers into a
:class:`RequestContext`, keeping the planes separate.

* ``Authorization: Bearer <agent token>`` — verified into a Principal. Required.
* ``X-Tool-Authorization: <user storage token>`` — optional delegated storage
  credential (toolAuthorization plane). It is wrapped opaquely and never logged.

If no tool credential is supplied the lifecycle falls back to the broker.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from a2a_workspace.container import Container
from a2a_workspace.errors import AuthorizationError
from a2a_workspace.identity.authorization import (
    CredentialKind,
    RequestContext,
    ToolCredential,
)
from a2a_workspace.storage.layout import WorkspaceLayout


def get_container(request: Request) -> Container:
    return request.app.state.container


def request_context(
    request: Request,
    authorization: str = Header(default=""),
    x_tool_authorization: str | None = Header(default=None),
) -> RequestContext:
    container: Container = request.app.state.container
    try:
        principal = container.identity.verify(authorization)
    except AuthorizationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    tool_credential = None
    if x_tool_authorization:
        # We bind the delegated token to this principal's workspace prefix so the
        # storage adapter's scope check has a concrete boundary. Storage IAM is
        # still the real authority on what the token can read.
        workspace_id = principal.derive_workspace_id(
            namespace=container.config.workspace_namespace
        )
        tool_credential = ToolCredential(
            kind=CredentialKind.DELEGATED_USER_OAUTH,
            secret=x_tool_authorization,
            scope_prefix=WorkspaceLayout(workspace_id).prefix,
        )

    return RequestContext(
        principal=principal,
        tool_credential=tool_credential,
        attributes={"path": request.url.path},
    )
