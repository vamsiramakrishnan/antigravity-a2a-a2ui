"""Content-addressing and integrity verification for skill trees.

A skill bundle's identity is a single ``content_digest`` computed over the whole
tree: for each file we hash ``relative_path \x00 sha256(bytes)``, sort those
per-file lines, and hash the concatenation. This is order-independent, sensitive
to both path and content, and cheap to recompute after a download.

The digest is the revision's name in storage and the value the materializer
re-checks. Recomputing it after download is what turns "the bytes I fetched"
into "the bytes that were approved": a mismatch means corruption or tampering,
and the only safe response is to refuse to run.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_files(files: dict[str, bytes]) -> str:
    """Compute the content digest of an in-memory ``{relpath: bytes}`` map."""
    lines: list[str] = []
    for path in sorted(files):
        _reject_unsafe_relpath(path)
        lines.append(f"{path}\x00{_sha256(files[path])}")
    blob = "\n".join(lines).encode()
    return _sha256(blob)


def content_digest_for_tree(root: Path | str) -> str:
    """Compute the content digest of a materialized directory tree."""
    root = Path(root)
    files: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            files[rel] = p.read_bytes()
    return digest_files(files)


def verify_tree(root: Path | str, *, expected_digest: str) -> None:
    """Raise ``IntegrityError`` unless ``root`` hashes to ``expected_digest``."""
    from a2a_workspace.errors import IntegrityError

    actual = content_digest_for_tree(root)
    if actual != expected_digest:
        raise IntegrityError(
            f"content digest mismatch: expected sha256-{expected_digest}, "
            f"materialized sha256-{actual}"
        )


def _reject_unsafe_relpath(path: str) -> None:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(seg in (".", "..") for seg in path.split("/"))
    ):
        raise ValueError(f"unsafe relative path in bundle: {path!r}")
