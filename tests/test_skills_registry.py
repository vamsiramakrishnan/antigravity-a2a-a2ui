from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from a2a_workspace.container import build_container
from a2a_workspace.gateway.app import create_app
from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.skill_io import export_skill_zip, import_skill_zip
from a2a_workspace.gemini_enterprise.skill_registry import SkillRegistryClient
from a2a_workspace.gemini_enterprise.transport import HttpResponse
from tests.test_gemini_enterprise import FakeDiscoveryTransport, _config


# -- ZIP round-trip --------------------------------------------------------


def test_export_import_zip_roundtrip():
    files = {
        "SKILL.md": b"---\nname: demo\n---\n# Demo",
        "scripts/run.py": b"print('hi')",
    }
    zip_bytes = export_skill_zip(files)
    back = import_skill_zip(zip_bytes)
    assert back == files


def test_export_requires_skill_md():
    with pytest.raises(ValueError):
        export_skill_zip({"notes.txt": b"x"})


def test_import_rejects_zip_slip():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", b"---\nname: x\n---")
        zf.writestr("../evil.sh", b"bad")
    with pytest.raises(ValueError):
        import_skill_zip(buf.getvalue())


def test_import_lowercase_skill_md_normalized():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skill.md", b"---\nname: x\n---")
    files = import_skill_zip(buf.getvalue())
    assert "SKILL.md" in files


# -- Skill Registry client -------------------------------------------------


class FakeRegistryTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.last_body = None

    def request(self, method, url, *, headers, body=None) -> HttpResponse:
        self.calls.append((method, url))
        self.last_body = json.loads(body) if body else None
        assert headers["Authorization"].startswith("Bearer ")
        if method == "POST" and url.rstrip("/").endswith("/skills") is False and ":retrieve" in url:
            return HttpResponse(
                200,
                json.dumps(
                    {"skills": [{"skill": {"name": ".../skills/legal-review", "displayName": "Legal Review"}}]}
                ).encode(),
            )
        if method == "POST":  # create
            return HttpResponse(200, json.dumps({"name": "operations/op-123"}).encode())
        if method == "GET" and ":retrieve" in url:
            return HttpResponse(
                200,
                json.dumps(
                    {"skills": [{"skill": {"name": ".../skills/legal-review", "displayName": "Legal Review", "description": "reviews contracts"}}]}
                ).encode(),
            )
        if method == "GET" and url.rstrip("/").endswith("/skills"):
            return HttpResponse(200, json.dumps({"skills": []}).encode())
        if method == "GET":  # get_skill
            files = {"SKILL.md": b"---\nname: legal\n---\n# Legal"}
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "name": ".../skills/legal-review",
                        "displayName": "Legal Review",
                        "zippedFilesystem": base64.b64encode(export_skill_zip(files)).decode(),
                    }
                ).encode(),
            )
        return HttpResponse(200, b"{}")


def _registry_config():
    return GeminiEnterpriseConfig(project="p", engine="app", skill_registry_location="us-central1")


def test_create_skill_sends_zipped_filesystem():
    transport = FakeRegistryTransport()
    client = SkillRegistryClient(
        config=_registry_config(), access_token="t", transport=transport
    )
    op = client.create_skill(
        "legal-review",
        display_name="Legal Review",
        description="reviews contracts",
        files={"SKILL.md": b"---\nname: legal\n---"},
    )
    assert op["name"] == "operations/op-123"
    assert "zippedFilesystem" in transport.last_body
    # URL carries the skillId query param.
    assert any("skillId=legal-review" in url for _, url in transport.calls)


def test_get_skill_decodes_files():
    client = SkillRegistryClient(
        config=_registry_config(), access_token="t", transport=FakeRegistryTransport()
    )
    skill = client.get_skill("legal-review")
    assert "SKILL.md" in skill.files
    assert skill.display_name == "Legal Review"


def test_retrieve_skills_semantic():
    client = SkillRegistryClient(
        config=_registry_config(), access_token="t", transport=FakeRegistryTransport()
    )
    results = client.retrieve_skills("review a contract")
    assert results[0].skill_id == "legal-review"


def test_invalid_skill_id_rejected():
    client = SkillRegistryClient(
        config=_registry_config(), access_token="t", transport=FakeRegistryTransport()
    )
    with pytest.raises(ValueError):
        client.create_skill("Bad_ID", display_name="x", description="y", files={"SKILL.md": b"x"})
    with pytest.raises(ValueError):
        client.create_skill("gcp-reserved", display_name="x", description="y", files={"SKILL.md": b"x"})


# -- workspace ZIP export/import + registry sync over HTTP ------------------


def _published_workspace(tmp_path):
    """Build an app + publish one skill; return (client, container, headers, digest)."""
    config = _config(tmp_path)
    container = build_container(config, discovery_transport=FakeRegistryTransport())
    app = create_app(config, container=container)
    client = TestClient(app)
    headers = {"Authorization": "Bearer https://idp|author|a@x.com"}

    did = client.post("/workspaces/me/drafts", headers=headers, json={}).json()["draft_id"]
    files = {
        "manifest.json": json.dumps({"name": "demo"}).encode(),
        "SKILL.md": b"---\nname: demo\n---\n# Demo",
    }
    for path, data in files.items():
        client.put(
            f"/workspaces/me/drafts/{did}/files",
            headers=headers,
            json={"path": path, "content_base64": base64.b64encode(data).decode()},
        )
    client.post(f"/workspaces/me/drafts/{did}/validate", headers=headers)
    client.post(f"/workspaces/me/drafts/{did}/submit", headers=headers)
    digest = client.post(
        f"/workspaces/me/drafts/{did}/publish", headers=headers, json={}
    ).json()["content_digest"]
    return client, container, headers, digest


def test_export_then_reimport_zip(tmp_path):
    client, _container, headers, digest = _published_workspace(tmp_path)

    # Export the revision as a ZIP.
    resp = client.get(f"/workspaces/me/revisions/{digest}/export-zip", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    files = import_skill_zip(resp.content)
    assert "SKILL.md" in files

    # Re-import the same ZIP -> same content digest (content-addressed).
    reimport = client.post(
        "/workspaces/me/skills/import-zip",
        headers={**headers, "X-Tool-Authorization": "user-token"},
        json={"zip_base64": base64.b64encode(resp.content).decode()},
    ).json()
    assert reimport["content_digest"] == digest


def test_push_revision_to_registry(tmp_path):
    client, container, headers, digest = _published_workspace(tmp_path)
    resp = client.post(
        "/workspaces/me/skills/registry-push",
        headers={**headers, "X-Tool-Authorization": "user-token"},
        json={"skill_id": "demo-skill", "digest": digest, "display_name": "Demo"},
    )
    assert resp.status_code == 200
    assert resp.json()["operation"] == "operations/op-123"


def test_import_skill_from_registry(tmp_path):
    client, container, headers, _digest = _published_workspace(tmp_path)
    resp = client.post(
        "/workspaces/me/skills/registry-import",
        headers={**headers, "X-Tool-Authorization": "user-token"},
        json={"skill_id": "legal-review"},
    )
    assert resp.status_code == 200
    assert resp.json()["content_digest"]
