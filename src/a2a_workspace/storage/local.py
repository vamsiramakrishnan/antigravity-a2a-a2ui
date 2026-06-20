"""A local-filesystem StorageAdapter for development and tests.

It stands in for a bucket by rooting every object under a directory. It still
goes through the same ``_guard`` checks as the GCS adapter, so tests that prove
isolation here prove it for the shared boundary logic too. It is *not* a
security boundary on its own — the local backend trusts the process — but it
faithfully exercises the layout and scoping rules.
"""

from __future__ import annotations

from pathlib import Path

from a2a_workspace.identity.authorization import ToolCredential
from a2a_workspace.storage.adapter import GuardedStorageAdapter, ObjectRef
from a2a_workspace.storage.layout import WorkspaceLayout


class LocalStorageAdapter(GuardedStorageAdapter):
    def __init__(
        self,
        *,
        root: Path | str,
        layout: WorkspaceLayout,
        credential: ToolCredential,
    ) -> None:
        super().__init__(layout=layout, credential=credential)
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root / self._guard(key)

    def get_object(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            from a2a_workspace.errors import NotFoundError

            raise NotFoundError(f"object not found: {key}")
        return path.read_bytes()

    def put_object(self, key: str, data: bytes) -> ObjectRef:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ObjectRef(key=key, size=len(data))

    def list_objects(self, prefix: str) -> list[ObjectRef]:
        self._guard(prefix)
        base = self._root / prefix
        if not base.exists():
            return []
        refs: list[ObjectRef] = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                key = str(p.relative_to(self._root))
                refs.append(ObjectRef(key=key, size=p.stat().st_size))
        return refs

    def delete_prefix(self, prefix: str) -> int:
        self._guard(prefix)
        base = self._root / prefix
        if not base.exists():
            return 0
        count = 0
        for p in sorted(base.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
                count += 1
            elif p.is_dir():
                p.rmdir()
        if base.is_dir():
            base.rmdir()
        return count
