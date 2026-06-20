from __future__ import annotations

import base64
import json

from a2a_workspace.messaging import (
    InputFile,
    materialize_inputs,
    parse_invocation,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_parse_text_and_file_parts_and_session():
    body = {
        "session": "projects/p/.../sessions/abc",
        "message": {
            "role": "user",
            "parts": [
                {"kind": "text", "text": "Summarize this"},
                {
                    "kind": "file",
                    "file": {
                        "name": "report.pdf",
                        "mimeType": "application/pdf",
                        "bytes": _b64(b"%PDF-1.4 fake"),
                    },
                },
            ],
        },
    }
    parsed = parse_invocation(body)
    assert parsed.prompt == "Summarize this"
    assert parsed.ge_session.endswith("/sessions/abc")
    assert len(parsed.files) == 1
    assert parsed.files[0].name == "report.pdf"
    assert parsed.files[0].data == b"%PDF-1.4 fake"


def test_parse_handles_uri_file_and_alternate_kind_key():
    body = {
        "message": {
            "parts": [
                {"type": "text", "text": "look at"},
                {"type": "file", "file": {"name": "x", "uri": "gs://bucket/x"}},
            ]
        }
    }
    parsed = parse_invocation(body)
    assert parsed.files[0].uri == "gs://bucket/x"
    assert parsed.files[0].data is None


def test_parse_empty_body_is_safe():
    parsed = parse_invocation(None)
    assert parsed.prompt == ""
    assert parsed.files == ()
    assert parsed.ge_session == ""


def test_materialize_inputs_writes_files_and_manifest(tmp_path):
    files = [
        InputFile(name="a.txt", mime_type="text/plain", data=b"hello"),
        InputFile(name="ref", mime_type="text/uri-list", uri="https://example/x"),
    ]
    manifest = materialize_inputs(tmp_path, files)
    assert (tmp_path / "a.txt").read_bytes() == b"hello"
    # URI-only file is recorded but not written.
    assert not (tmp_path / "ref").exists()
    saved = json.loads((tmp_path / ".manifest.json").read_text())
    assert {e["name"] for e in saved} == {"a.txt", "ref"}
    assert any(e.get("uri") == "https://example/x" for e in manifest)


def test_materialize_inputs_sanitizes_path_traversal(tmp_path):
    files = [InputFile(name="../../etc/passwd", mime_type="text/plain", data=b"x")]
    materialize_inputs(tmp_path, files)
    # The file lands inside inputs/ as a bare name; no escape.
    assert (tmp_path / "passwd").read_bytes() == b"x"
    assert not (tmp_path.parent.parent / "etc" / "passwd").exists()
