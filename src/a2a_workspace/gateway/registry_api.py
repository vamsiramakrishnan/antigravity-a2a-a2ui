"""Workspace/Registry REST API — the A2UI file-browser backend.

Every route resolves the workspace from the *verified principal* ("me"), never
from a path parameter, so a caller can only ever act on their own workspace. The
routes expose exactly the bounded tools — create draft, patch, validate, submit,
publish/activate — and nothing that would let the model write storage directly.

This is the "lightweight file-browser mode" backend from the architecture: the
browser IDE calls these endpoints; there is no terminal and no raw bucket
credential on this surface.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from a2a_workspace.container import Container
from a2a_workspace.errors import (
    ConflictError,
    IsolationError,
    NotFoundError,
    ValidationError,
)
from a2a_workspace.gateway.dependencies import get_container, request_context
from a2a_workspace.identity.authorization import RequestContext
from a2a_workspace.storage.layout import WorkspaceLayout

router = APIRouter(prefix="/workspaces/me", tags=["workspace"])


# -- request models --------------------------------------------------------


class CreateDraftBody(BaseModel):
    base_digest: str | None = None


class PatchBody(BaseModel):
    path: str
    # base64 so binary skill assets round-trip; null deletes the file.
    content_base64: str | None = Field(default=None)


class PublishBody(BaseModel):
    activate: bool = True
    note: str = ""


# -- helpers ---------------------------------------------------------------


def _my_workspace(ctx: RequestContext, container: Container):
    return container.registry.ensure_workspace(
        ctx.principal,
        organization=container.config.organization,
        environment=container.config.environment,
        region=container.config.storage.region,
    )


def _assert_owns_draft(container: Container, draft_id: str, workspace_id: str):
    draft = container.drafts.get_draft(draft_id)
    if draft.workspace_id != workspace_id:
        # Same response as "not found": never confirm another tenant's draft id.
        raise NotFoundError(f"draft not found: {draft_id}")
    return draft


# -- routes ----------------------------------------------------------------


@router.get("")
def get_my_workspace(
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    ws = _my_workspace(ctx, container)
    return {
        "workspace_id": ws.workspace_id,
        "organization": ws.organization,
        "environment": ws.environment,
        "region": ws.region,
        "active_generation": ws.active_generation,
        "display_email": ws.display_email,
    }


@router.post("/drafts")
def create_draft(
    body: CreateDraftBody,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    ws = _my_workspace(ctx, container)
    draft = container.drafts.create_draft(ws.workspace_id, base_digest=body.base_digest)
    return {"draft_id": draft.draft_id, "status": draft.status.value}


@router.put("/drafts/{draft_id}/files")
def patch_file(
    draft_id: str,
    body: PatchBody,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    ws = _my_workspace(ctx, container)
    _assert_owns_draft(container, draft_id, ws.workspace_id)
    content = (
        base64.b64decode(body.content_base64)
        if body.content_base64 is not None
        else None
    )
    try:
        draft = container.drafts.apply_patch(draft_id, path=body.path, content=content)
    except (ConflictError, ValidationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"draft_id": draft.draft_id, "files": sorted(draft.files), "status": draft.status.value}


@router.post("/drafts/{draft_id}/validate")
def validate_draft(
    draft_id: str,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    ws = _my_workspace(ctx, container)
    _assert_owns_draft(container, draft_id, ws.workspace_id)
    draft = container.drafts.validate(draft_id)
    return {
        "draft_id": draft.draft_id,
        "status": draft.status.value,
        "errors": draft.validation_errors,
    }


@router.post("/drafts/{draft_id}/submit")
def submit_draft(
    draft_id: str,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    ws = _my_workspace(ctx, container)
    _assert_owns_draft(container, draft_id, ws.workspace_id)
    try:
        draft = container.drafts.submit(draft_id)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"draft_id": draft.draft_id, "status": draft.status.value}


@router.post("/drafts/{draft_id}/publish")
def publish_draft(
    draft_id: str,
    body: PublishBody,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    ws = _my_workspace(ctx, container)
    _assert_owns_draft(container, draft_id, ws.workspace_id)

    # Publication needs a write-capable, workspace-scoped credential. In a real
    # deployment this is the *publisher* identity, not the gateway SA. Here we
    # mint a workspace-scoped credential with write permissions.
    credential = ctx.tool_credential or container.broker.issue(
        principal=ctx.principal,
        workspace_id=ws.workspace_id,
        permissions=("storage.objects.create", "storage.objects.get"),
    )
    storage = container.storage_factory(WorkspaceLayout(ws.workspace_id), credential)
    try:
        result = container.drafts.publish(
            draft_id, storage=storage, activate=body.activate, note=body.note
        )
    except (ValidationError, ConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IsolationError as exc:  # pragma: no cover - defense in depth
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "content_digest": result.content_digest,
        "activated_generation": result.activated_generation,
    }
