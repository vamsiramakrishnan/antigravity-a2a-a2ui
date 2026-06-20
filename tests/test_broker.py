from __future__ import annotations

import pytest

from a2a_workspace.broker.broker import (
    DownscopedCredentialBroker,
    perms_are_read_only,
)
from a2a_workspace.errors import IsolationError
from a2a_workspace.identity.authorization import CredentialKind
from a2a_workspace.identity.principal import Principal
from a2a_workspace.registry.memory import InMemoryRegistry
from a2a_workspace.storage.layout import WorkspaceLayout


def _registry_with_workspace(principal):
    reg = InMemoryRegistry()
    ws = reg.ensure_workspace(
        principal, organization="acme", environment="test", region="local"
    )
    return reg, ws


def test_downscoped_broker_binds_credential_to_workspace_prefix():
    p = Principal(issuer="https://idp", subject="u1")
    reg, ws = _registry_with_workspace(p)
    captured = {}

    def minter(boundary, ttl):
        captured["boundary"] = boundary
        captured["ttl"] = ttl
        return "minted-token"

    broker = DownscopedCredentialBroker(
        bucket="skills-test", registry=reg, minter=minter, ttl_seconds=300
    )
    cred = broker.issue(principal=p, workspace_id=ws.workspace_id)

    assert cred.kind is CredentialKind.DOWNSCOPED_BROKER
    assert cred.secret == "minted-token"
    assert cred.scope_prefix == WorkspaceLayout(ws.workspace_id).prefix
    # The access-boundary condition restricts to exactly this workspace prefix.
    expr = captured["boundary"]["accessBoundary"]["accessBoundaryRules"][0][
        "availabilityCondition"
    ]["expression"]
    assert ws.workspace_id in expr
    assert captured["ttl"] == 300


def test_downscoped_broker_refuses_foreign_workspace():
    alice = Principal(issuer="https://idp", subject="alice")
    bob = Principal(issuer="https://idp", subject="bob")
    reg, alice_ws = _registry_with_workspace(alice)
    reg.ensure_workspace(bob, organization="acme", environment="test", region="local")

    broker = DownscopedCredentialBroker(
        bucket="skills-test", registry=reg, minter=lambda b, t: "tok"
    )
    # Bob asks for Alice's workspace -> refused before any credential is minted.
    with pytest.raises(IsolationError):
        broker.issue(principal=bob, workspace_id=alice_ws.workspace_id)


def test_read_only_permission_detection():
    assert perms_are_read_only(("storage.objects.get", "storage.objects.list"))
    assert not perms_are_read_only(("storage.objects.create",))
