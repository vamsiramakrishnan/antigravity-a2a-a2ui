"""Bounded publication tools: the only way skills get written.

The model never writes to object storage or to the active revision. It works a
mutable :class:`Draft` through a small, validated surface:

    create_draft -> apply_patch* -> validate -> submit -> (registry) publish

``publish`` is the privileged step and is intentionally *not* something the model
can call freely: it computes the immutable content digest, writes the revision
bytes to storage under ``revisions/sha256-{digest}/``, records the revision in
the registry, and (optionally) activates it as a new generation. Because the
revision path is the digest, publishing the same content twice is idempotent and
can never overwrite an existing revision.

Validation is policy's hook point. The default checks are structural (a manifest
must exist and parse, paths must be safe); an organization layers its own skill
policy on top by passing ``extra_validators``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from a2a_workspace.errors import ConflictError, NotFoundError, ValidationError
from a2a_workspace.materializer.integrity import digest_files
from a2a_workspace.registry.models import Draft, DraftStatus, Revision
from a2a_workspace.registry.registry import WorkspaceRegistry
from a2a_workspace.storage.adapter import StorageAdapter

Validator = Callable[[Draft], list[str]]


@dataclass(frozen=True, slots=True)
class PublishResult:
    content_digest: str
    activated_generation: int | None


class DraftService:
    """Stateful holder of open drafts plus the publish pipeline.

    Drafts live in memory keyed by id; in a multi-instance deployment they would
    live in the registry/storage drafts prefix. The publish path, by contrast,
    always goes through the durable registry + storage.
    """

    def __init__(
        self,
        *,
        registry: WorkspaceRegistry,
        extra_validators: tuple[Validator, ...] = (),
    ) -> None:
        self._registry = registry
        self._drafts: dict[str, Draft] = {}
        self._validators: tuple[Validator, ...] = (
            _validate_paths,
            _validate_manifest,
            *extra_validators,
        )

    # -- bounded, model-callable tools -------------------------------------

    def create_draft(self, workspace_id: str, *, base_digest: str | None = None) -> Draft:
        draft = Draft(
            draft_id=f"draft_{uuid.uuid4().hex[:12]}",
            workspace_id=workspace_id,
            base_digest=base_digest,
        )
        if base_digest is not None:
            # Seed from an existing revision's manifest so edits are incremental.
            rev = self._registry.get_revision(workspace_id, base_digest)
            draft.files["manifest.json"] = json.dumps(
                rev.manifest, indent=2, sort_keys=True
            ).encode()
        self._drafts[draft.draft_id] = draft
        return draft

    def apply_patch(self, draft_id: str, *, path: str, content: bytes | None) -> Draft:
        """Add/update (``content``) or delete (``content is None``) one file."""
        draft = self._get_open_draft(draft_id)
        if content is None:
            draft.files.pop(path, None)
        else:
            draft.files[path] = content
        draft.status = DraftStatus.OPEN
        draft.validation_errors = []
        return draft

    def validate(self, draft_id: str) -> Draft:
        draft = self._get_open_draft(draft_id)
        errors: list[str] = []
        for validator in self._validators:
            errors.extend(validator(draft))
        draft.validation_errors = errors
        draft.status = DraftStatus.VALIDATED if not errors else DraftStatus.OPEN
        return draft

    def submit(self, draft_id: str) -> Draft:
        draft = self._get_open_draft(draft_id)
        if draft.status is not DraftStatus.VALIDATED:
            raise ValidationError("draft must pass validate() before submit()")
        draft.status = DraftStatus.SUBMITTED
        return draft

    # -- privileged publication (registry-side, not a free agent tool) ------

    def publish(
        self,
        draft_id: str,
        *,
        storage: StorageAdapter,
        activate: bool = True,
        note: str = "",
    ) -> PublishResult:
        """Write the submitted draft as an immutable revision.

        Requires a workspace-scoped ``StorageAdapter`` with write permission —
        i.e. a credential carrying ``storage.objects.create``. The model does not
        hold such a credential; a publisher identity does.
        """
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise NotFoundError(f"draft not found: {draft_id}")
        if draft.status is not DraftStatus.SUBMITTED:
            raise ValidationError("only a submitted draft can be published")
        if storage.layout.workspace_id != draft.workspace_id:
            raise ConflictError("storage adapter is bound to a different workspace")

        digest = digest_files(draft.files)
        manifest = json.loads(draft.files["manifest.json"].decode())

        layout = storage.layout
        # Write the full draft tree under the immutable, content-addressed
        # prefix. The materializer downloads this exact set and recomputes the
        # digest, so everything that contributes to the digest must live here —
        # including manifest.json.
        skills_prefix = layout.revision_skills_prefix(digest)
        for rel_path, data in draft.files.items():
            storage.put_object(skills_prefix + rel_path, data)
        # Also write a standalone manifest copy at the revision root for cheap
        # metadata reads (listing, policy) without materializing the whole tree.
        storage.put_object(
            layout.revision_manifest_key(digest),
            json.dumps(manifest, indent=2, sort_keys=True).encode(),
        )

        revision = Revision(
            workspace_id=draft.workspace_id,
            content_digest=digest,
            manifest=manifest,
            parent_digest=draft.base_digest,
        )
        self._registry.add_revision(revision)

        generation = None
        if activate:
            gen = self._registry.activate_revision(
                draft.workspace_id, digest, note=note
            )
            generation = gen.number

        draft.status = DraftStatus.PUBLISHED
        return PublishResult(content_digest=digest, activated_generation=generation)

    # -- helpers -----------------------------------------------------------

    def get_draft(self, draft_id: str) -> Draft:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise NotFoundError(f"draft not found: {draft_id}")
        return draft

    def _get_open_draft(self, draft_id: str) -> Draft:
        draft = self.get_draft(draft_id)
        if draft.status in (DraftStatus.PUBLISHED, DraftStatus.ABANDONED):
            raise ConflictError(f"draft {draft_id} is {draft.status.value}")
        return draft


# -- default validators ----------------------------------------------------


def _validate_paths(draft: Draft) -> list[str]:
    errors: list[str] = []
    for path in draft.files:
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or any(seg in (".", "..") for seg in path.split("/"))
        ):
            errors.append(f"unsafe path: {path!r}")
    return errors


def _validate_manifest(draft: Draft) -> list[str]:
    raw = draft.files.get("manifest.json")
    if raw is None:
        return ["manifest.json is required"]
    try:
        manifest = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"manifest.json is not valid JSON: {exc}"]
    errors = []
    if not isinstance(manifest, dict):
        errors.append("manifest.json must be a JSON object")
    elif "name" not in manifest:
        errors.append("manifest.json must declare a 'name'")
    return errors
