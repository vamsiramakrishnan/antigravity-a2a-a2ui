"""Convert skill bundles to/from the agentskills.io ZIP format.

Both Gemini Enterprise surfaces speak this format: the assistant's Skills (import
a ZIP with ``SKILL.md`` at the root) and the Skill Registry (a skill *is* a
base64-encoded ``zippedFilesystem`` of ``SKILL.md`` + ``scripts/`` + ``references/``
+ ``assets/``). Because our workspace revisions are already ``SKILL.md`` bundles,
these two pure functions are the whole bridge: export a revision to a GE-importable
ZIP, or import a GE skill ZIP for publication as a workspace revision.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

# agentskills.io requires a SKILL.md at the archive root. We accept either case
# on import and normalize to SKILL.md.
_SKILL_FILE_CANDIDATES = ("SKILL.md", "skill.md")


def export_skill_zip(files: dict[str, bytes]) -> bytes:
    """Pack a ``{relpath: bytes}`` skill bundle into a ZIP archive."""
    if not any(name in files for name in _SKILL_FILE_CANDIDATES):
        raise ValueError("skill bundle must contain a SKILL.md at its root")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in sorted(files.items()):
            _reject_unsafe(rel)
            zf.writestr(rel, data)
    return buf.getvalue()


def import_skill_zip(zip_bytes: bytes) -> dict[str, bytes]:
    """Read a skill ZIP into a ``{relpath: bytes}`` map, validating it.

    Rejects archives without a root ``SKILL.md`` and any entry whose path would
    escape the bundle root (zip-slip). Normalizes a root ``skill.md`` to
    ``SKILL.md`` so downstream code has one spelling.
    """
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = info.filename
            _reject_unsafe(rel)
            files[rel] = zf.read(info)

    if "SKILL.md" not in files and "skill.md" in files:
        files["SKILL.md"] = files.pop("skill.md")
    if "SKILL.md" not in files:
        raise ValueError("skill ZIP has no SKILL.md at its root")
    return files


def _reject_unsafe(rel: str) -> None:
    p = PurePosixPath(rel)
    if p.is_absolute() or rel.startswith("/") or "\\" in rel:
        raise ValueError(f"unsafe path in skill archive: {rel!r}")
    if any(part in ("..", "") for part in p.parts):
        raise ValueError(f"unsafe path in skill archive: {rel!r}")
