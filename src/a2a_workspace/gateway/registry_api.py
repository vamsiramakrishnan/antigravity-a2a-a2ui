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
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from a2a_workspace.container import Container
from a2a_workspace.errors import (
    ConflictError,
    IsolationError,
    NotFoundError,
    ValidationError,
)
from a2a_workspace.gateway.dependencies import get_container, request_context
from a2a_workspace.gemini_enterprise.skill_io import export_skill_zip, import_skill_zip
from a2a_workspace.identity.authorization import RequestContext
from a2a_workspace.registry.models import DraftStatus
from a2a_workspace.storage.adapter import StorageAdapter
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


class ImportZipBody(BaseModel):
    zip_base64: str
    activate: bool = True


class RegistryPushBody(BaseModel):
    skill_id: str
    digest: str
    display_name: str = ""
    description: str = ""
    create: bool = True


class RegistryImportBody(BaseModel):
    skill_id: str
    activate: bool = True


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


def _storage_for(ctx, container, ws, *, write: bool) -> StorageAdapter:
    perms = (
        ("storage.objects.create", "storage.objects.get")
        if write
        else ("storage.objects.get", "storage.objects.list")
    )
    credential = ctx.tool_credential or container.broker.issue(
        principal=ctx.principal, workspace_id=ws.workspace_id, permissions=perms
    )
    return container.storage_factory(WorkspaceLayout(ws.workspace_id), credential)


def _read_revision_files(
    storage: StorageAdapter, workspace_id: str, digest: str
) -> dict[str, bytes]:
    layout = WorkspaceLayout(workspace_id)
    try:
        prefix = layout.revision_skills_prefix(digest)
    except ValueError as exc:
        raise NotFoundError(f"bad revision digest: {digest}") from exc
    files: dict[str, bytes] = {}
    for ref in storage.list_objects(prefix):
        rel = ref.key[len(prefix) :]
        if rel:
            files[rel] = storage.get_object(ref.key)
    if not files:
        raise NotFoundError(f"revision not found: {digest}")
    return files


def _publish_files(
    container: Container, workspace_id: str, files: dict[str, bytes], storage, *, activate, note
):
    draft = container.drafts.create_draft(workspace_id)
    for path, content in files.items():
        container.drafts.apply_patch(draft.draft_id, path=path, content=content)
    validated = container.drafts.validate(draft.draft_id)
    if validated.status is not DraftStatus.VALIDATED:
        raise ValidationError("; ".join(validated.validation_errors))
    container.drafts.submit(draft.draft_id)
    return container.drafts.publish(
        draft.draft_id, storage=storage, activate=activate, note=note
    )


def _ensure_manifest(files: dict[str, bytes], *, default_name: str) -> dict[str, bytes]:
    """GE skills carry SKILL.md but not our manifest.json; synthesize one."""
    if "manifest.json" in files:
        return files
    name = default_name
    md = files.get("SKILL.md", b"").decode(errors="ignore")
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("name:"):
            name = s[len("name:") :].strip() or default_name
            break
    return {**files, "manifest.json": json.dumps({"name": name}).encode()}


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


# -- agentskills.io ZIP interop + Skill Registry sync ----------------------


@router.get("/revisions/{digest}/export-zip")
def export_revision_zip(
    digest: str,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> Response:
    """Download a workspace revision as a GE-importable agentskills.io ZIP."""
    ws = _my_workspace(ctx, container)
    storage = _storage_for(ctx, container, ws, write=False)
    files = _read_revision_files(storage, ws.workspace_id, digest)
    zip_bytes = export_skill_zip(files)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="skill-{digest[:12]}.zip"'
        },
    )


@router.post("/skills/import-zip")
def import_skill_zip_endpoint(
    body: ImportZipBody,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    """Import a GE/agentskills.io skill ZIP and publish it as a revision."""
    ws = _my_workspace(ctx, container)
    try:
        files = import_skill_zip(base64.b64decode(body.zip_base64))
    except Exception as exc:  # bad base64 / bad zip / missing SKILL.md
        raise HTTPException(status_code=400, detail=f"invalid skill ZIP: {exc}") from exc
    files = _ensure_manifest(files, default_name="imported-skill")
    storage = _storage_for(ctx, container, ws, write=True)
    result = _publish_files(
        container, ws.workspace_id, files, storage, activate=body.activate, note="import-zip"
    )
    return {
        "content_digest": result.content_digest,
        "activated_generation": result.activated_generation,
    }


@router.post("/skills/registry-push")
def push_to_skill_registry(
    body: RegistryPushBody,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    """Publish a workspace revision to the Gemini Enterprise Skill Registry."""
    ws = _my_workspace(ctx, container)
    if ctx.tool_credential is None:
        raise HTTPException(
            status_code=400, detail="a delegated credential is required to push"
        )
    storage = _storage_for(ctx, container, ws, write=False)
    files = _read_revision_files(storage, ws.workspace_id, body.digest)
    client = container.skill_registry_client_factory(ctx.tool_credential.secret)
    try:
        if body.create:
            op = client.create_skill(
                body.skill_id,
                display_name=body.display_name or body.skill_id,
                description=body.description,
                files=files,
            )
        else:
            op = client.update_skill(
                body.skill_id,
                files=files,
                display_name=body.display_name or None,
                description=body.description or None,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"skill_id": body.skill_id, "operation": op.get("name", "")}


@router.post("/skills/registry-import")
def import_from_skill_registry(
    body: RegistryImportBody,
    ctx: RequestContext = Depends(request_context),
    container: Container = Depends(get_container),
) -> dict:
    """Pull a skill from the Skill Registry and publish it as a workspace revision."""
    ws = _my_workspace(ctx, container)
    if ctx.tool_credential is None:
        raise HTTPException(
            status_code=400, detail="a delegated credential is required to import"
        )
    client = container.skill_registry_client_factory(ctx.tool_credential.secret)
    skill = client.get_skill(body.skill_id)
    if not skill.files:
        raise HTTPException(status_code=404, detail=f"skill has no files: {body.skill_id}")
    files = _ensure_manifest(skill.files, default_name=skill.display_name or body.skill_id)
    storage = _storage_for(ctx, container, ws, write=True)
    result = _publish_files(
        container,
        ws.workspace_id,
        files,
        storage,
        activate=body.activate,
        note=f"registry-import:{body.skill_id}",
    )
    return {
        "content_digest": result.content_digest,
        "activated_generation": result.activated_generation,
    }
