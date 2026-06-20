"""A Cloud Storage StorageAdapter using a per-request, workspace-scoped credential.

This is the production storage path. It is import-guarded: ``google-cloud-storage``
is an optional extra, and importing this module without it raises a clear error
rather than letting the service start in a degraded state.

The crucial property: the client is built from the *request's* ``ToolCredential``,
not from the Cloud Run service account. That credential is either

* a delegated end-user OAuth token (storage evaluates the user's own IAM on the
  managed folder), or
* a downscoped credential whose Credential Access Boundary restricts it to this
  workspace's prefix.

Either way, a confused-deputy bug here cannot read another tenant's data, because
the credential in hand simply lacks the permission. ``_guard`` is belt-and-braces
on top of that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a_workspace.errors import NotFoundError
from a2a_workspace.identity.authorization import CredentialKind, ToolCredential
from a2a_workspace.storage.adapter import GuardedStorageAdapter, ObjectRef
from a2a_workspace.storage.layout import WorkspaceLayout

if TYPE_CHECKING:  # pragma: no cover
    from google.cloud import storage as gcs_storage


def _load_storage():
    try:
        from google.cloud import storage  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ImportError(
            "GcsStorageAdapter requires the 'gcp' extra: pip install "
            "'antigravity-a2a-a2ui[gcp]'"
        ) from exc
    return storage, Credentials


class GcsStorageAdapter(GuardedStorageAdapter):
    def __init__(
        self,
        *,
        bucket: str,
        layout: WorkspaceLayout,
        credential: ToolCredential,
        _client: "gcs_storage.Client | None" = None,
    ) -> None:
        super().__init__(layout=layout, credential=credential)
        self._bucket_name = bucket
        self._client = _client or self._build_client(credential)
        self._bucket = self._client.bucket(bucket)

    @staticmethod
    def _build_client(credential: ToolCredential):
        storage, Credentials = _load_storage()
        # Both supported kinds present as an OAuth2 bearer token to the client.
        # For DOWNSCOPED_BROKER the token already carries its Credential Access
        # Boundary; for DELEGATED_USER_OAUTH it is the user's access token.
        if credential.kind not in (
            CredentialKind.DELEGATED_USER_OAUTH,
            CredentialKind.DOWNSCOPED_BROKER,
        ):
            raise ValueError(f"unsupported credential kind: {credential.kind}")
        creds = Credentials(token=credential.secret)
        return storage.Client(credentials=creds)

    def get_object(self, key: str) -> bytes:
        blob = self._bucket.blob(self._guard(key))
        data = blob.download_as_bytes()
        if data is None:  # pragma: no cover - client raises instead, defensive
            raise NotFoundError(f"object not found: {key}")
        return data

    def put_object(self, key: str, data: bytes) -> ObjectRef:
        blob = self._bucket.blob(self._guard(key))
        blob.upload_from_string(data)
        return ObjectRef(key=key, size=len(data), etag=getattr(blob, "etag", None))

    def list_objects(self, prefix: str) -> list[ObjectRef]:
        self._guard(prefix)
        refs: list[ObjectRef] = []
        for blob in self._client.list_blobs(self._bucket_name, prefix=prefix):
            refs.append(
                ObjectRef(key=blob.name, size=blob.size or 0, etag=blob.etag)
            )
        return refs

    def delete_prefix(self, prefix: str) -> int:
        self._guard(prefix)
        count = 0
        for blob in self._client.list_blobs(self._bucket_name, prefix=prefix):
            blob.delete()
            count += 1
        return count
