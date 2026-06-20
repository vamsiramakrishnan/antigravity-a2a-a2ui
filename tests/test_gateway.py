from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from a2a_workspace.container import build_container
from a2a_workspace.gateway.app import create_app


def _client(config):
    container = build_container(config)
    app = create_app(config, container=container)
    return TestClient(app), container


def _auth(issuer="https://idp", subject="user-1", email="u@x.com"):
    return {"Authorization": f"Bearer {issuer}|{subject}|{email}"}


def test_healthz(config):
    client, _ = _client(config)
    assert client.get("/healthz").json()["status"] == "ok"


def test_agent_card_advertises_two_planes(config):
    client, _ = _client(config)
    card = client.get("/.well-known/agent.json").json()
    assert "agentAuthorization" in card
    assert card["toolAuthorizations"][0]["id"] == "workspace-storage"
    assert card["toolAuthorizations"][0]["optional"] is True


def test_invoke_requires_authentication(config):
    client, _ = _client(config)
    resp = client.post("/a2a/invoke")
    assert resp.status_code == 401


def test_full_authoring_then_invoke_flow(config):
    client, _ = _client(config)
    headers = _auth(subject="author-1")

    # Create a workspace by asking for "me".
    me = client.get("/workspaces/me", headers=headers).json()
    assert me["active_generation"] is None

    # Author a skill through the bounded tools.
    draft_id = client.post(
        "/workspaces/me/drafts", headers=headers, json={}
    ).json()["draft_id"]

    def put_file(path, data: bytes):
        return client.put(
            f"/workspaces/me/drafts/{draft_id}/files",
            headers=headers,
            json={"path": path, "content_base64": base64.b64encode(data).decode()},
        )

    put_file("manifest.json", json.dumps({"name": "greeter"}).encode())
    put_file("skill.py", b"print('hello')")

    validated = client.post(
        f"/workspaces/me/drafts/{draft_id}/validate", headers=headers
    ).json()
    assert validated["errors"] == []

    client.post(f"/workspaces/me/drafts/{draft_id}/submit", headers=headers)
    published = client.post(
        f"/workspaces/me/drafts/{draft_id}/publish",
        headers=headers,
        json={"activate": True},
    ).json()
    assert published["activated_generation"] == 1

    # Now invoking returns a pinned conversation + a credential-free connection.
    invoked = client.post("/a2a/invoke", headers=headers).json()
    assert invoked["generation"] == 1
    assert invoked["content_digest"] == published["content_digest"]
    conn = invoked["connection"]
    assert conn["read_only_skills"] is True
    assert conn["skills_paths"]
    # No token leaked into the response payload.
    assert "secret" not in json.dumps(invoked).lower()


def test_invoke_before_publish_returns_conflict(config):
    client, _ = _client(config)
    headers = _auth(subject="empty-1")
    resp = client.post("/a2a/invoke", headers=headers)
    assert resp.status_code == 409


def test_one_user_cannot_touch_another_users_draft(config):
    client, _ = _client(config)
    alice = _auth(subject="alice")
    bob = _auth(subject="bob")

    draft_id = client.post("/workspaces/me/drafts", headers=alice, json={}).json()[
        "draft_id"
    ]
    # Bob tries to patch Alice's draft id -> looks like "not found".
    resp = client.put(
        f"/workspaces/me/drafts/{draft_id}/files",
        headers=bob,
        json={"path": "x", "content_base64": base64.b64encode(b"x").decode()},
    )
    assert resp.status_code == 404
