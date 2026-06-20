from __future__ import annotations

import uuid

import pytest

from a2a_workspace.errors import IsolationError
from a2a_workspace.identity.authorization import CredentialKind, ToolCredential
from a2a_workspace.storage.layout import WorkspaceLayout
from a2a_workspace.storage.local import LocalStorageAdapter


def _wsid() -> str:
    return str(uuid.uuid4())


def test_layout_contains_blocks_traversal_and_other_tenants():
    a = WorkspaceLayout(_wsid())
    b = WorkspaceLayout(_wsid())
    assert a.contains(a.prefix + "drafts/x")
    assert not a.contains(b.prefix + "drafts/x")
    # path traversal that would climb into another workspace is normalised away
    assert not a.contains(a.prefix + "../" + b.workspace_id + "/secret")


def test_revision_prefix_requires_valid_digest():
    layout = WorkspaceLayout(_wsid())
    with pytest.raises(ValueError):
        layout.revision_prefix("not-a-digest")
    good = "a" * 64
    assert layout.revision_prefix(good).endswith(f"revisions/sha256-{good}/")


def test_adapter_refuses_keys_outside_workspace(tmp_path):
    layout = WorkspaceLayout(_wsid())
    other = WorkspaceLayout(_wsid())
    cred = ToolCredential(
        kind=CredentialKind.DELEGATED_USER_OAUTH,
        secret="t",
        scope_prefix=layout.prefix,
    )
    adapter = LocalStorageAdapter(root=tmp_path, layout=layout, credential=cred)

    adapter.put_object(layout.prefix + "drafts/d/file", b"ok")
    assert adapter.get_object(layout.prefix + "drafts/d/file") == b"ok"

    # Reaching into another workspace's prefix is refused at the app layer.
    with pytest.raises(IsolationError):
        adapter.get_object(other.prefix + "drafts/d/file")


def test_adapter_refuses_keys_outside_credential_scope(tmp_path):
    layout = WorkspaceLayout(_wsid())
    # Credential scoped to a *narrower* prefix than the workspace.
    narrow = layout.prefix + "revisions/"
    cred = ToolCredential(
        kind=CredentialKind.DOWNSCOPED_BROKER,
        secret="t",
        scope_prefix=narrow,
    )
    adapter = LocalStorageAdapter(root=tmp_path, layout=layout, credential=cred)

    # Inside the credential scope: fine.
    digest = "b" * 64
    adapter.put_object(layout.revision_skills_prefix(digest) + "s.py", b"x")
    # Inside the workspace but outside the credential scope: refused.
    with pytest.raises(IsolationError):
        adapter.put_object(layout.prefix + "drafts/d/file", b"x")
