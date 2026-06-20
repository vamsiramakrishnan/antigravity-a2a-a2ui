from __future__ import annotations

import pytest

from a2a_workspace.errors import IntegrityError
from a2a_workspace.identity.authorization import RequestContext
from a2a_workspace.identity.principal import Principal
from a2a_workspace.storage.layout import WorkspaceLayout
from tests.conftest import publish_simple_skill


def _ctx(principal: Principal) -> RequestContext:
    return RequestContext(principal=principal)


def test_start_materializes_verified_session(container):
    p = Principal(issuer="https://idp", subject="u-life")
    publish_simple_skill(container, p)
    started = container.lifecycle.start(_ctx(p))

    # Conversation is pinned to the active generation.
    assert started.conversation.generation == 1
    # Connection exposes filesystem paths and NO credential.
    assert started.connection.read_only_skills is True
    assert started.materialized.skills_dir.exists()
    # The materialized skill file is present and correct.
    assert (started.materialized.skills_dir / "skill.py").read_bytes()


def test_connection_never_carries_a_credential(container):
    p = Principal(issuer="https://idp", subject="u-cred")
    publish_simple_skill(container, p)
    started = container.lifecycle.start(_ctx(p))
    conn = started.connection
    # The opaque tool credential is not anywhere on the connection.
    flat = repr(conn).lower()
    assert "delegated:" not in flat
    assert "secret" not in flat
    # env is empty and structurally credential-free (guard would have raised).
    assert conn.env == {}


def test_conversation_pins_generation_across_new_activations(container):
    p = Principal(issuer="https://idp", subject="u-pin")
    publish_simple_skill(container, p, name="v1", body=b"v1")
    started_v1 = container.lifecycle.start(_ctx(p))
    assert started_v1.conversation.generation == 1
    digest_v1 = started_v1.conversation.content_digest

    # Publish + activate a new revision.
    publish_simple_skill(container, p, name="v2", body=b"v2-body")
    started_v2 = container.lifecycle.start(_ctx(p))
    assert started_v2.conversation.generation == 2
    assert started_v2.conversation.content_digest != digest_v1

    # The old conversation is unchanged: still pinned to generation 1's digest.
    assert started_v1.conversation.content_digest == digest_v1


def test_materializer_rejects_tampered_storage(container):
    p = Principal(issuer="https://idp", subject="u-tamper")
    result = publish_simple_skill(container, p)
    ws = container.registry.ensure_workspace(
        p, organization="acme", environment="test", region="local"
    )
    layout = WorkspaceLayout(ws.workspace_id)

    # Tamper with the stored skill bytes after publication.
    from a2a_workspace.identity.authorization import CredentialKind, ToolCredential

    cred = ToolCredential(
        kind=CredentialKind.DELEGATED_USER_OAUTH, secret="t", scope_prefix=layout.prefix
    )
    storage = container.storage_factory(layout, cred)
    skill_key = layout.revision_skills_prefix(result.content_digest) + "skill.py"
    storage.put_object(skill_key, b"print('tampered')")

    with pytest.raises(IntegrityError):
        container.lifecycle.start(_ctx(p))


def test_two_principals_get_isolated_workspaces(container):
    a = Principal(issuer="https://idp", subject="alice")
    b = Principal(issuer="https://idp", subject="bob")
    publish_simple_skill(container, a, body=b"alice-skill")
    publish_simple_skill(container, b, body=b"bob-skill")

    sa = container.lifecycle.start(_ctx(a))
    sb = container.lifecycle.start(_ctx(b))
    assert sa.conversation.workspace_id != sb.conversation.workspace_id
    assert (sa.materialized.skills_dir / "skill.py").read_bytes() == b"alice-skill"
    assert (sb.materialized.skills_dir / "skill.py").read_bytes() == b"bob-skill"
