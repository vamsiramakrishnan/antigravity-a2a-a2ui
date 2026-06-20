from __future__ import annotations

import json
from pathlib import Path

import pytest

from a2a_workspace.config import (
    Config,
    IdentityConfig,
    RegistryConfig,
    SessionConfig,
    StorageConfig,
)
from a2a_workspace.container import build_container


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        organization="acme",
        environment="test",
        storage=StorageConfig(
            backend="local",
            bucket="skills-test",
            local_root=str(tmp_path / "bucket"),
            region="local",
        ),
        identity=IdentityConfig(backend="dev", allow_insecure_dev=True),
        registry=RegistryConfig(backend="memory"),
        session=SessionConfig(materialization_root=str(tmp_path / "sessions")),
    )


@pytest.fixture
def container(config):
    return build_container(config)


def publish_simple_skill(container, principal, *, name="hello", body=b"print('hi')"):
    """Helper: create -> patch -> validate -> submit -> publish a tiny skill."""
    ws = container.registry.ensure_workspace(
        principal, organization="acme", environment="test", region="local"
    )
    draft = container.drafts.create_draft(ws.workspace_id)
    container.drafts.apply_patch(
        draft.draft_id,
        path="manifest.json",
        content=json.dumps({"name": name}).encode(),
    )
    container.drafts.apply_patch(
        draft.draft_id, path="skill.py", content=body
    )
    container.drafts.validate(draft.draft_id)
    container.drafts.submit(draft.draft_id)
    from a2a_workspace.storage.layout import WorkspaceLayout

    credential = container.broker.issue(
        principal=principal,
        workspace_id=ws.workspace_id,
        permissions=("storage.objects.create",),
    )
    storage = container.storage_factory(WorkspaceLayout(ws.workspace_id), credential)
    return container.drafts.publish(draft.draft_id, storage=storage, activate=True)
