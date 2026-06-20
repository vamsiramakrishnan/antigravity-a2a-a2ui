"""Short-lived, session-scoped proxy tokens.

This realizes the SCION-style "per-agent scoped credential": a small HMAC-signed
token that authorizes an Antigravity session to call back into the control
plane's enterprise-proxy endpoints *as a specific principal*, for a short window,
and nothing else. It is intentionally not the user's OAuth token — it cannot
read storage, cannot be refreshed, and is useless once expired.

It is the capability the agent runtime *is* allowed to hold; the durable user
credential stays behind the gateway (see ``SessionCredentialStore``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from a2a_workspace.errors import AuthorizationError


@dataclass(frozen=True, slots=True)
class SessionToken:
    principal_key: str
    conversation_id: str
    expires_at_epoch: float


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


class SessionTokenService:
    """Mints and verifies session proxy tokens with a shared HMAC secret.

    The secret is process/deployment-held (a Secret Manager value in prod). A
    leaked *session* token only grants the bearer the same enterprise-proxy
    access the agent already had, briefly — not access to storage or the raw user
    credential.
    """

    def __init__(self, *, secret: bytes, ttl_seconds: int = 900) -> None:
        if len(secret) < 16:
            raise ValueError("session token secret must be at least 16 bytes")
        self._secret = secret
        self._ttl = ttl_seconds

    def mint(self, *, principal_key: str, conversation_id: str) -> str:
        payload = {
            "prn": principal_key,
            "cid": conversation_id,
            "exp": time.time() + self._ttl,
        }
        raw = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64url(self._sign(raw))
        return f"{raw}.{sig}"

    def verify(self, token: str) -> SessionToken:
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        try:
            raw, sig = token.split(".", 1)
        except ValueError as exc:
            raise AuthorizationError("malformed session token") from exc
        if not hmac.compare_digest(_b64url(self._sign(raw)), sig):
            raise AuthorizationError("session token signature mismatch")
        payload = json.loads(_b64url_decode(raw))
        if payload.get("exp", 0) < time.time():
            raise AuthorizationError("session token expired")
        return SessionToken(
            principal_key=payload["prn"],
            conversation_id=payload["cid"],
            expires_at_epoch=payload["exp"],
        )

    def _sign(self, raw: str) -> bytes:
        return hmac.new(self._secret, raw.encode(), hashlib.sha256).digest()


class SessionCredentialStore:
    """Holds the user's Discovery Engine token for the life of a session.

    This is the control-plane side of the decrypt boundary: the user's delegated
    token is associated with a conversation here and used by the enterprise-proxy
    endpoints, but it is never handed to the Antigravity runtime. In production
    this is backed by the Gemini Enterprise auth manager (encrypted at rest,
    refreshed centrally); the in-memory version mirrors the contract for dev.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._sessions: dict[str, str] = {}

    def put(
        self, conversation_id: str, ge_access_token: str, *, ge_session: str = ""
    ) -> None:
        self._tokens[conversation_id] = ge_access_token
        if ge_session:
            self._sessions[conversation_id] = ge_session

    def get(self, conversation_id: str) -> str | None:
        return self._tokens.get(conversation_id)

    def get_session(self, conversation_id: str) -> str:
        """Return the Discovery Engine session for this conversation, or ''.

        Lets the enterprise proxy reuse the same streamAssist session so connector
        calls see the conversation history and uploaded context files.
        """
        return self._sessions.get(conversation_id, "")

    def drop(self, conversation_id: str) -> None:
        self._tokens.pop(conversation_id, None)
        self._sessions.pop(conversation_id, None)
