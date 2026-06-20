"""Parse the inbound A2A invocation that Gemini Enterprise sends.

When Gemini Enterprise routes a ``streamAssist`` call to a registered agent, it
invokes this service over A2A with a ``Message`` whose ``parts`` carry the user's
prompt (``TextPart``) and any uploaded files (``FilePart``), plus the Discovery
Engine ``session`` that ties the turn to its uploaded context.

This module turns that wire payload into:

* a plain prompt string,
* a list of :class:`InputFile` (decoded bytes, or a URI reference), and
* the carried-through ``session`` name,

and materializes the files into the session's ``inputs/`` directory so the agent
can open and operate on them as real files rather than conversation text.

The parser is deliberately permissive about the exact A2A part encoding (``kind``
vs ``type``; ``file.bytes`` vs ``file.uri``) because that shape is still settling
across A2A revisions.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InputFile:
    name: str
    mime_type: str = "application/octet-stream"
    data: bytes | None = None  # decoded inline content, if provided
    uri: str | None = None  # reference, if the file was passed by URI


@dataclass(frozen=True, slots=True)
class ParsedInvocation:
    prompt: str
    files: tuple[InputFile, ...] = ()
    ge_session: str = ""


def parse_invocation(body: dict | None) -> ParsedInvocation:
    """Parse an A2A invocation body into prompt + files + session."""
    if not body:
        return ParsedInvocation(prompt="")

    # The session may sit at the top level or inside the message.
    ge_session = str(body.get("session") or "")

    message = body.get("message") or {}
    parts = message.get("parts") or []

    prompt_chunks: list[str] = []
    files: list[InputFile] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind") or part.get("type") or _infer_kind(part)
        if kind == "text" and part.get("text"):
            prompt_chunks.append(str(part["text"]))
        elif kind == "file":
            f = _parse_file_part(part.get("file") or part)
            if f is not None:
                files.append(f)
        # "data" parts (structured JSON) are ignored for now; the agent gets the
        # prompt + files, which is what it operates on.

    return ParsedInvocation(
        prompt="\n".join(prompt_chunks).strip(),
        files=tuple(files),
        ge_session=ge_session,
    )


def materialize_inputs(inputs_dir: Path | str, files) -> list[dict]:
    """Write parsed files into ``inputs_dir`` and return a manifest.

    Inline-byte files are written to disk; URI-only files are recorded in the
    manifest (the agent resolves them through the connector tools). A
    ``.manifest.json`` is always written so the agent can enumerate its inputs.
    """
    base = Path(inputs_dir)
    base.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for f in files:
        safe = _safe_name(f.name)
        entry = {"name": safe, "mime_type": f.mime_type}
        if f.data is not None:
            (base / safe).write_bytes(f.data)
            entry["path"] = str(base / safe)
        if f.uri:
            entry["uri"] = f.uri
        manifest.append(entry)
    (base / ".manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


# -- helpers ---------------------------------------------------------------


def _infer_kind(part: dict) -> str:
    if "text" in part:
        return "text"
    if "file" in part:
        return "file"
    if "data" in part:
        return "data"
    return ""


def _parse_file_part(file_obj: dict) -> InputFile | None:
    if not isinstance(file_obj, dict):
        return None
    name = str(file_obj.get("name") or "upload.bin")
    mime = str(file_obj.get("mimeType") or file_obj.get("mime_type") or "application/octet-stream")
    raw = file_obj.get("bytes")
    data: bytes | None = None
    if isinstance(raw, str) and raw:
        try:
            data = base64.b64decode(raw)
        except (ValueError, TypeError):
            data = None
    uri = file_obj.get("uri") or file_obj.get("url")
    if data is None and not uri:
        return None
    return InputFile(name=name, mime_type=mime, data=data, uri=str(uri) if uri else None)


def _safe_name(name: str) -> str:
    """Strip any path components so an upload name cannot escape ``inputs/``."""
    base = Path(str(name)).name
    if not base or base in (".", ".."):
        return "upload.bin"
    return base
