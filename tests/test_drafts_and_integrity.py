from __future__ import annotations

import json

import pytest

from a2a_workspace.errors import IntegrityError, ValidationError
from a2a_workspace.identity.principal import Principal
from a2a_workspace.materializer.integrity import digest_files, verify_tree
from a2a_workspace.registry.models import DraftStatus
from a2a_workspace.storage.layout import WorkspaceLayout
from tests.conftest import publish_simple_skill


def test_digest_is_order_independent_and_content_sensitive():
    a = digest_files({"x": b"1", "y": b"2"})
    b = digest_files({"y": b"2", "x": b"1"})
    assert a == b
    c = digest_files({"x": b"1", "y": b"changed"})
    assert a != c


def test_verify_tree_detects_tampering(tmp_path):
    files = {"skill.py": b"print('ok')", "manifest.json": b'{"name":"x"}'}
    digest = digest_files(files)
    for rel, data in files.items():
        (tmp_path / rel).write_bytes(data)
    verify_tree(tmp_path, expected_digest=digest)  # passes

    (tmp_path / "skill.py").write_bytes(b"print('evil')")
    with pytest.raises(IntegrityError):
        verify_tree(tmp_path, expected_digest=digest)


def test_validate_blocks_submit_until_clean(container):
    p = Principal(issuer="https://idp", subject="u1")
    ws = container.registry.ensure_workspace(
        p, organization="acme", environment="test", region="local"
    )
    draft = container.drafts.create_draft(ws.workspace_id)
    # No manifest yet -> validation fails -> cannot submit.
    container.drafts.apply_patch(draft.draft_id, path="skill.py", content=b"x")
    validated = container.drafts.validate(draft.draft_id)
    assert validated.status is DraftStatus.OPEN
    assert any("manifest" in e for e in validated.validation_errors)
    with pytest.raises(ValidationError):
        container.drafts.submit(draft.draft_id)


def test_publish_is_content_addressed_and_idempotent(container):
    p = Principal(issuer="https://idp", subject="u-dup")
    r1 = publish_simple_skill(container, p, name="same", body=b"same-body")
    r2 = publish_simple_skill(container, p, name="same", body=b"same-body")
    # Identical content -> identical digest. Two generations, one revision.
    assert r1.content_digest == r2.content_digest
    assert r2.activated_generation == r1.activated_generation + 1


def test_published_revision_matches_materialization_digest(container):
    p = Principal(issuer="https://idp", subject="u-mat")
    result = publish_simple_skill(container, p)
    ws = container.registry.ensure_workspace(
        p, organization="acme", environment="test", region="local"
    )
    # Re-read the stored skills tree and confirm it hashes to the revision digest.
    layout = WorkspaceLayout(ws.workspace_id)
    from a2a_workspace.identity.authorization import CredentialKind, ToolCredential

    cred = ToolCredential(
        kind=CredentialKind.DELEGATED_USER_OAUTH,
        secret="t",
        scope_prefix=layout.prefix,
    )
    storage = container.storage_factory(layout, cred)
    prefix = layout.revision_skills_prefix(result.content_digest)
    files = {
        ref.key[len(prefix) :]: storage.get_object(ref.key)
        for ref in storage.list_objects(prefix)
    }
    assert digest_files(files) == result.content_digest
