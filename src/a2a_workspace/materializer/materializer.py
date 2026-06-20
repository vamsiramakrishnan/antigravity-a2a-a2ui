"""Download an immutable revision into an isolated, verified local directory.

The materializer is the bridge between "immutable bytes in object storage" and
"a directory Antigravity can read". It:

1. creates a fresh per-session directory (never reused across sessions),
2. downloads the revision's ``skills/`` tree through the *trusted* storage
   adapter (so the per-request credential, not a broad SA, fetches the bytes),
3. optionally layers a read-only global catalog underneath,
4. re-verifies the content digest, and refuses the session on mismatch.

It hands back a :class:`MaterializedSession` whose ``skills_dir`` is read-only to
the runtime and whose ``app_data_dir`` is the only writable scratch area.
"""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from a2a_workspace.materializer.integrity import verify_tree
from a2a_workspace.registry.models import Generation
from a2a_workspace.storage.adapter import StorageAdapter


@dataclass(frozen=True, slots=True)
class MaterializedSession:
    session_id: str
    workspace_id: str
    generation: int
    content_digest: str
    root: Path
    skills_dir: Path
    app_data_dir: Path

    def cleanup(self) -> None:
        """Remove the whole session tree. Safe to call more than once."""
        shutil.rmtree(self.root, ignore_errors=True)


class SessionMaterializer:
    def __init__(
        self,
        *,
        root: Path | str,
        global_catalog_path: Path | str | None = None,
    ) -> None:
        self._root = Path(root)
        self._catalog = Path(global_catalog_path) if global_catalog_path else None

    def materialize(
        self,
        *,
        storage: StorageAdapter,
        generation: Generation,
    ) -> MaterializedSession:
        workspace_id = storage.layout.workspace_id
        if generation.workspace_id != workspace_id:
            raise ValueError("generation/storage workspace mismatch")

        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        session_root = self._root / workspace_id / session_id
        skills_dir = session_root / "skills"
        app_data_dir = session_root / "antigravity-data"
        skills_dir.mkdir(parents=True, exist_ok=True)
        app_data_dir.mkdir(parents=True, exist_ok=True)

        # 1. Lay down the read-only global catalog first (if any), so the
        #    workspace revision wins on any path collision.
        if self._catalog and self._catalog.is_dir():
            shutil.copytree(self._catalog, skills_dir, dirs_exist_ok=True)

        # 2. Download the immutable revision through the trusted adapter.
        digest = generation.content_digest
        skills_prefix = storage.layout.revision_skills_prefix(digest)
        for ref in storage.list_objects(skills_prefix):
            rel = ref.key[len(skills_prefix) :]
            if not rel:
                continue
            target = skills_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(storage.get_object(ref.key))

        # 3. Verify the materialized tree matches the approved digest. If a
        #    global catalog was layered in, it participates in the digest only if
        #    the revision was built to include it; otherwise verify the revision
        #    subset. We verify the revision-owned files explicitly.
        self._verify_revision_subset(skills_dir, storage, digest)

        # 4. Make the skills tree read-only to the runtime.
        _make_readonly(skills_dir)

        return MaterializedSession(
            session_id=session_id,
            workspace_id=workspace_id,
            generation=generation.number,
            content_digest=digest,
            root=session_root,
            skills_dir=skills_dir,
            app_data_dir=app_data_dir,
        )

    def _verify_revision_subset(
        self, skills_dir: Path, storage: StorageAdapter, digest: str
    ) -> None:
        """Verify that the revision-owned files materialized exactly.

        When no global catalog is present the whole ``skills_dir`` is the
        revision, so a full-tree digest check is exact. When a catalog is layered
        in we verify the revision files individually against storage, since they
        are the security-relevant, content-addressed set.
        """
        if not self._catalog:
            verify_tree(skills_dir, expected_digest=digest)
            return

        skills_prefix = storage.layout.revision_skills_prefix(digest)
        # Re-fetch each revision object and compare bytes on disk.
        import hashlib

        for ref in storage.list_objects(skills_prefix):
            rel = ref.key[len(skills_prefix) :]
            if not rel:
                continue
            on_disk = (skills_dir / rel).read_bytes()
            if hashlib.sha256(on_disk).digest() != hashlib.sha256(
                storage.get_object(ref.key)
            ).digest():
                from a2a_workspace.errors import IntegrityError

                raise IntegrityError(f"revision file {rel} failed verification")


def _make_readonly(path: Path) -> None:
    """Strip write bits from a tree. Hygiene, not a hard sandbox.

    For trusted declarative skills this is enough to keep the runtime from
    mutating the materialized revision in place. Executable user scripts need a
    real sandbox (separate job, restricted egress) — see the provisioning notes.
    """
    ro = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    ro_dir = ro | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    for current, dirs, files in os.walk(path):
        for f in files:
            os.chmod(os.path.join(current, f), ro)
        for d in dirs:
            os.chmod(os.path.join(current, d), ro_dir)
    os.chmod(path, ro_dir)
