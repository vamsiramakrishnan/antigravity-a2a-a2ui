"""Client for the Gemini Enterprise Skill Registry (Vertex AI Platform API).

This is the programmatic way to manage skills: full CRUD plus immutable
revisions and a semantic ``RetrieveSkills`` search, on
``{location}-aiplatform.googleapis.com/v1beta1/.../skills``. A skill *is* a
base64-encoded ``zippedFilesystem`` of ``SKILL.md`` + ``scripts/`` + ``references/``
+ ``assets/`` — the same agentskills.io format our workspace revisions use, so
:mod:`a2a_workspace.gemini_enterprise.skill_io` is the only translation needed.

Constructed with the caller's access token (the user's, or a publisher identity
with ``aiplatform`` scope). Transport is injectable for testing. Create/update/
delete are long-running operations; the raw operation dict is returned.
"""

from __future__ import annotations

import base64
import json
import re

from a2a_workspace.gemini_enterprise.config import GeminiEnterpriseConfig
from a2a_workspace.gemini_enterprise.skill_io import export_skill_zip, import_skill_zip
from a2a_workspace.gemini_enterprise.transport import (
    HttpResponse,
    Transport,
    UrllibTransport,
)

_SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


class RegistrySkill:
    """A skill resource. ``files`` is populated only by ``get_skill``."""

    def __init__(
        self,
        *,
        name: str,
        display_name: str = "",
        description: str = "",
        state: str = "",
        files: dict[str, bytes] | None = None,
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.description = description
        self.state = state
        self.files = files or {}

    @property
    def skill_id(self) -> str:
        return self.name.rsplit("/", 1)[-1]


class SkillRegistryClient:
    def __init__(
        self,
        *,
        config: GeminiEnterpriseConfig,
        access_token: str,
        transport: Transport | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("SkillRegistryClient requires an access token")
        if not config.skill_registry_configured():
            raise ValueError("GeminiEnterpriseConfig needs a project for the registry")
        self._config = config
        self._token = access_token
        self._transport = transport or UrllibTransport()

    # -- management --------------------------------------------------------

    def create_skill(
        self,
        skill_id: str,
        *,
        display_name: str,
        description: str,
        files: dict[str, bytes],
    ) -> dict:
        """Register a new skill from a SKILL.md bundle. Returns the LRO."""
        _validate_skill_id(skill_id)
        body = {
            "displayName": display_name,
            "description": description,
            "zippedFilesystem": _zip_b64(files),
        }
        resp = self._post(f"{self._skills_url()}?skillId={skill_id}", body)
        return resp.json() or {}

    def update_skill(
        self,
        skill_id: str,
        *,
        files: dict[str, bytes],
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Publish a new revision of an existing skill. Returns the LRO."""
        _validate_skill_id(skill_id)
        body: dict = {"zippedFilesystem": _zip_b64(files)}
        mask = ["zippedFilesystem"]
        if display_name is not None:
            body["displayName"] = display_name
            mask.append("displayName")
        if description is not None:
            body["description"] = description
            mask.append("description")
        url = f"{self._skill_url(skill_id)}?updateMask={','.join(mask)}"
        resp = self._transport.request(
            "PATCH", url, headers=self._headers(), body=json.dumps(body).encode()
        )
        return resp.json() or {}

    def get_skill(self, skill_id: str) -> RegistrySkill:
        resp = self._get(self._skill_url(skill_id))
        return _parse_skill(resp.json() or {}, with_files=True)

    def list_skills(self) -> list[RegistrySkill]:
        resp = self._get(self._skills_url())
        data = resp.json() or {}
        return [_parse_skill(s) for s in data.get("skills", [])]

    def delete_skill(self, skill_id: str) -> dict:
        resp = self._transport.request(
            "DELETE", self._skill_url(skill_id), headers=self._headers()
        )
        return resp.json() or {}

    def retrieve_skills(self, query: str) -> list[RegistrySkill]:
        """Semantic search: let an agent discover relevant skills by intent."""
        from urllib.parse import quote

        resp = self._get(f"{self._skills_url()}:retrieve?query={quote(query)}")
        data = resp.json() or {}
        # Response may nest matches under "skills" or "results".
        items = data.get("skills") or data.get("results") or []
        return [_parse_skill(s.get("skill", s)) for s in items]

    # -- URLs / HTTP -------------------------------------------------------

    def _skills_url(self) -> str:
        return f"{self._config.skill_registry_base_url}/{self._config.skills_parent}/skills"

    def _skill_url(self, skill_id: str) -> str:
        return f"{self._config.skill_registry_base_url}/{self._config.skills_parent}/skills/{skill_id}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _post(self, url: str, body: dict) -> HttpResponse:
        return self._transport.request(
            "POST", url, headers=self._headers(), body=json.dumps(body).encode()
        )

    def _get(self, url: str) -> HttpResponse:
        return self._transport.request("GET", url, headers=self._headers())


def _zip_b64(files: dict[str, bytes]) -> str:
    return base64.b64encode(export_skill_zip(files)).decode()


def _parse_skill(d: dict, *, with_files: bool = False) -> RegistrySkill:
    files = None
    if with_files and d.get("zippedFilesystem"):
        files = import_skill_zip(base64.b64decode(d["zippedFilesystem"]))
    return RegistrySkill(
        name=d.get("name", ""),
        display_name=d.get("displayName", ""),
        description=d.get("description", ""),
        state=d.get("state", ""),
        files=files,
    )


def _validate_skill_id(skill_id: str) -> None:
    if not _SKILL_ID_RE.match(skill_id):
        raise ValueError(
            "skill id must be 1-63 chars, lowercase letters/digits/hyphens, "
            f"start with a letter: {skill_id!r}"
        )
    if skill_id.startswith("gcp-"):
        raise ValueError("skill id must not start with the reserved 'gcp-' prefix")
