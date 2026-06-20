"""In-memory registry for development and tests.

Thread-safe enough for the test suite and a single-process dev server. The
Firestore adapter (:mod:`a2a_workspace.registry.firestore`) mirrors this shape
with documents and a transaction around generation bumps.
"""

from __future__ import annotations

import threading

from a2a_workspace.config import WORKSPACE_NAMESPACE
from a2a_workspace.errors import NotFoundError
from a2a_workspace.identity.principal import Principal
from a2a_workspace.registry.models import Generation, Revision, Workspace


class InMemoryRegistry:
    def __init__(self, *, namespace=WORKSPACE_NAMESPACE) -> None:
        self._namespace = namespace
        self._lock = threading.RLock()
        self._workspaces: dict[str, Workspace] = {}
        self._by_principal: dict[str, str] = {}
        self._revisions: dict[tuple[str, str], Revision] = {}
        self._generations: dict[str, list[Generation]] = {}

    def ensure_workspace(
        self, principal: Principal, *, organization: str, environment: str, region: str
    ) -> Workspace:
        with self._lock:
            existing = self._by_principal.get(principal.key)
            if existing is not None:
                return self._workspaces[existing]
            workspace_id = principal.derive_workspace_id(namespace=self._namespace)
            ws = Workspace(
                workspace_id=workspace_id,
                principal_key=principal.key,
                organization=organization,
                environment=environment,
                region=region,
                display_email=principal.email,
            )
            self._workspaces[workspace_id] = ws
            self._by_principal[principal.key] = workspace_id
            self._generations.setdefault(workspace_id, [])
            return ws

    def get_workspace(self, workspace_id: str) -> Workspace:
        with self._lock:
            ws = self._workspaces.get(workspace_id)
            if ws is None:
                raise NotFoundError(f"workspace not found: {workspace_id}")
            return ws

    def workspace_owner(self, workspace_id: str) -> str | None:
        with self._lock:
            ws = self._workspaces.get(workspace_id)
            return ws.principal_key if ws else None

    def add_revision(self, revision: Revision) -> Revision:
        with self._lock:
            if revision.workspace_id not in self._workspaces:
                raise NotFoundError(f"workspace not found: {revision.workspace_id}")
            key = (revision.workspace_id, revision.content_digest)
            # Content-addressed: re-adding identical content is a no-op.
            self._revisions.setdefault(key, revision)
            return self._revisions[key]

    def get_revision(self, workspace_id: str, content_digest: str) -> Revision:
        with self._lock:
            rev = self._revisions.get((workspace_id, content_digest))
            if rev is None:
                raise NotFoundError(
                    f"revision sha256-{content_digest} not in {workspace_id}"
                )
            return rev

    def activate_revision(
        self, workspace_id: str, content_digest: str, *, note: str = ""
    ) -> Generation:
        with self._lock:
            # Revision must exist before it can be activated.
            self.get_revision(workspace_id, content_digest)
            gens = self._generations.setdefault(workspace_id, [])
            number = (gens[-1].number + 1) if gens else 1
            gen = Generation(
                workspace_id=workspace_id,
                number=number,
                content_digest=content_digest,
                note=note,
            )
            gens.append(gen)
            self._workspaces[workspace_id].active_generation = number
            return gen

    def resolve_active_generation(self, workspace_id: str) -> Generation:
        with self._lock:
            gens = self._generations.get(workspace_id) or []
            if not gens:
                raise NotFoundError(
                    f"workspace {workspace_id} has no active generation yet"
                )
            return gens[-1]

    def get_generation(self, workspace_id: str, number: int) -> Generation:
        with self._lock:
            for gen in self._generations.get(workspace_id, []):
                if gen.number == number:
                    return gen
            raise NotFoundError(f"generation {number} not in {workspace_id}")
