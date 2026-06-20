"""Hooks: audit trail + secret redaction.

The pure, testable piece is :func:`redact_secrets` — a regex scrubber for the
common credential shapes (bearer tokens, ``ya29.`` access tokens, ``AIza`` API
keys, PEM private keys). The SDK realizers (:func:`audit_hooks`,
:func:`redaction_hook`) are import-guarded.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# ---------------------------------------------------------------------------
# Pure redaction
# ---------------------------------------------------------------------------

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization: Bearer <token>
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    # Google OAuth2 access tokens
    (re.compile(r"ya29\.[A-Za-z0-9._\-]+"), "[REDACTED_ACCESS_TOKEN]"),
    # Google API keys
    (re.compile(r"AIza[A-Za-z0-9._\-]{10,}"), "[REDACTED_API_KEY]"),
    # PEM private key blocks (any flavor: RSA/EC/OPENSSH/generic)
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
)


def redact_secrets(text: str) -> str:
    """Replace recognizable secrets in ``text`` with placeholder markers.

    Order matters: the bearer rule runs first so a ``Bearer ya29...`` header is
    collapsed wholesale rather than leaving a dangling prefix.
    """
    if not text:
        return text
    out = text
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


# ---------------------------------------------------------------------------
# Import-guarded realizers
# ---------------------------------------------------------------------------


def audit_hooks(emit: Callable[[dict], None]) -> list:
    """Return audit hooks that call ``emit`` with structured event dicts.

    Covers session start/end, every completed tool call, and tool errors. Each
    hook is read-only (Inspect-style) — it observes and reports, never blocks.
    Raises ``ImportError`` if the SDK is missing.
    """
    try:
        from google.antigravity import hooks  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional SDK
        raise ImportError(
            "audit_hooks requires the Antigravity SDK: pip install "
            "google-antigravity"
        ) from exc

    @hooks.on_session_start
    def _on_session_start(ctx):  # pragma: no cover - requires SDK runtime
        emit({"event": "session_start", "session": _session_id(ctx)})

    @hooks.on_session_end
    def _on_session_end(ctx):  # pragma: no cover - requires SDK runtime
        emit({"event": "session_end", "session": _session_id(ctx)})

    @hooks.post_tool_call
    def _post_tool_call(ctx):  # pragma: no cover - requires SDK runtime
        emit(
            {
                "event": "tool_call",
                "tool": _attr(ctx, "tool", "tool_name"),
                "session": _session_id(ctx),
            }
        )

    @hooks.on_tool_error
    def _on_tool_error(ctx):  # pragma: no cover - requires SDK runtime
        emit(
            {
                "event": "tool_error",
                "tool": _attr(ctx, "tool", "tool_name"),
                "error": str(_attr(ctx, "error", "exception")),
                "session": _session_id(ctx),
            }
        )

    return [_on_session_start, _on_session_end, _post_tool_call, _on_tool_error]


def redaction_hook(redact: Callable[[str], str]) -> object:
    """Return a ``pre_turn`` TransformHook that runs ``redact`` over outbound
    turn text, scrubbing secrets before they reach the model/logs.

    Raises ``ImportError`` if the SDK is missing.
    """
    try:
        from google.antigravity import hooks  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional SDK
        raise ImportError(
            "redaction_hook requires the Antigravity SDK: pip install "
            "google-antigravity"
        ) from exc

    @hooks.pre_turn
    def _redact_pre_turn(ctx):  # pragma: no cover - requires SDK runtime
        text = _attr(ctx, "text", "content", "message")
        if isinstance(text, str):
            return redact(text)
        return None

    return _redact_pre_turn


def _attr(obj, *names):  # pragma: no cover - SDK runtime helper
    for name in names:
        val = getattr(obj, name, None)
        if val is not None:
            return val
    return None


def _session_id(ctx):  # pragma: no cover - SDK runtime helper
    return _attr(ctx, "conversation_id", "session_id", "session")
