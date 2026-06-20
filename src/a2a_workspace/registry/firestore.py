"""Firestore-backed registry (production shape).

Import-guarded behind the ``gcp`` extra. Mirrors :class:`InMemoryRegistry` but
persists to documents and wraps the generation bump in a transaction so two
concurrent activations cannot produce duplicate generation numbers.

Layout:
    workspaces/{workspace_id}                      -> Workspace
    workspaces/{workspace_id}/revisions/{digest}   -> Revision
    workspaces/{workspace_id}/generations/{number} -> Generation
    principal_index/{principal_key}                 -> {workspace_id}
"""

from __future__ import annotations

from a2a_workspace.errors import NotFoundError
from a2a_workspace.identity.principal import Principal
from a2a_workspace.registry.models import Generation, Revision, Workspace


def _load_firestore():
    try:
        from google.cloud import firestore  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional extra
        raise ImportError(
            "FirestoreRegistry requires the 'gcp' extra: pip install "
            "'antigravity-a2a-a2ui[gcp]'"
        ) from exc
    return firestore


class FirestoreRegistry:
    def __init__(self, *, project: str, database: str, namespace) -> None:
        firestore = _load_firestore()
        self._fs = firestore
        self._db = firestore.Client(project=project, database=database)
        self._namespace = namespace

    def ensure_workspace(
        self, principal: Principal, *, organization: str, environment: str, region: str
    ) -> Workspace:
        index = self._db.collection("principal_index").document(principal.key)
        snapshot = index.get()
        if snapshot.exists:
            return self.get_workspace(snapshot.get("workspace_id"))

        workspace_id = principal.derive_workspace_id(namespace=self._namespace)
        ws = Workspace(
            workspace_id=workspace_id,
            principal_key=principal.key,
            organization=organization,
            environment=environment,
            region=region,
            display_email=principal.email,
        )
        # Create both docs; index doubles as the uniqueness guard.
        self._db.collection("workspaces").document(workspace_id).set(_ws_to_doc(ws))
        index.set({"workspace_id": workspace_id})
        return ws

    def get_workspace(self, workspace_id: str) -> Workspace:
        snap = self._db.collection("workspaces").document(workspace_id).get()
        if not snap.exists:
            raise NotFoundError(f"workspace not found: {workspace_id}")
        return _doc_to_ws(snap.to_dict())

    def workspace_owner(self, workspace_id: str) -> str | None:
        snap = self._db.collection("workspaces").document(workspace_id).get()
        return snap.get("principal_key") if snap.exists else None

    def add_revision(self, revision: Revision) -> Revision:
        ref = (
            self._db.collection("workspaces")
            .document(revision.workspace_id)
            .collection("revisions")
            .document(revision.content_digest)
        )
        # create() fails if it exists; revisions are immutable so we tolerate that.
        if not ref.get().exists:
            ref.set(_rev_to_doc(revision))
        return revision

    def get_revision(self, workspace_id: str, content_digest: str) -> Revision:
        snap = (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("revisions")
            .document(content_digest)
            .get()
        )
        if not snap.exists:
            raise NotFoundError(f"revision sha256-{content_digest} not in {workspace_id}")
        return _doc_to_rev(snap.to_dict())

    def activate_revision(
        self, workspace_id: str, content_digest: str, *, note: str = ""
    ) -> Generation:
        self.get_revision(workspace_id, content_digest)  # must exist
        ws_ref = self._db.collection("workspaces").document(workspace_id)
        gens = ws_ref.collection("generations")

        @self._fs.transactional
        def _bump(txn) -> Generation:
            ws_snap = ws_ref.get(transaction=txn)
            current = ws_snap.get("active_generation") or 0
            number = current + 1
            gen = Generation(
                workspace_id=workspace_id, number=number, content_digest=content_digest, note=note
            )
            txn.set(gens.document(str(number)), _gen_to_doc(gen))
            txn.update(ws_ref, {"active_generation": number})
            return gen

        return _bump(self._db.transaction())

    def resolve_active_generation(self, workspace_id: str) -> Generation:
        ws = self.get_workspace(workspace_id)
        if ws.active_generation is None:
            raise NotFoundError(f"workspace {workspace_id} has no active generation yet")
        return self.get_generation(workspace_id, ws.active_generation)

    def get_generation(self, workspace_id: str, number: int) -> Generation:
        snap = (
            self._db.collection("workspaces")
            .document(workspace_id)
            .collection("generations")
            .document(str(number))
            .get()
        )
        if not snap.exists:
            raise NotFoundError(f"generation {number} not in {workspace_id}")
        return _doc_to_gen(snap.to_dict())


# -- (de)serialization -----------------------------------------------------


def _ws_to_doc(ws: Workspace) -> dict:
    return {
        "workspace_id": ws.workspace_id,
        "principal_key": ws.principal_key,
        "organization": ws.organization,
        "environment": ws.environment,
        "region": ws.region,
        "created_at_epoch": ws.created_at_epoch,
        "display_email": ws.display_email,
        "active_generation": ws.active_generation,
    }


def _doc_to_ws(d: dict) -> Workspace:
    return Workspace(**d)


def _rev_to_doc(r: Revision) -> dict:
    return {
        "workspace_id": r.workspace_id,
        "content_digest": r.content_digest,
        "manifest": r.manifest,
        "created_at_epoch": r.created_at_epoch,
        "parent_digest": r.parent_digest,
    }


def _doc_to_rev(d: dict) -> Revision:
    return Revision(**d)


def _gen_to_doc(g: Generation) -> dict:
    return {
        "workspace_id": g.workspace_id,
        "number": g.number,
        "content_digest": g.content_digest,
        "activated_at_epoch": g.activated_at_epoch,
        "note": g.note,
    }


def _doc_to_gen(d: dict) -> Generation:
    return Generation(**d)
