"""Turning an inbound agent-authorization token into a verified Principal.

This is the *agentAuthorization* plane: it answers "who is invoking the agent?"
and nothing else. It must never trust unsigned claims, and it must never read an
identity out of the request body.

Two implementations are provided:

* :class:`JwtIdentityVerifier` — verifies a signed JWT against a JWKS and the
  expected issuer/audience. This is the production path.
* :class:`DevIdentityVerifier` — accepts a tiny unsigned ``iss:sub`` token for
  local development and tests. It refuses to be constructed unless explicitly
  marked insecure, so it cannot be enabled by accident in a deployed service.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import jwt
from jwt import PyJWKClient

from a2a_workspace.errors import AuthorizationError
from a2a_workspace.identity.principal import Principal


@runtime_checkable
class IdentityVerifier(Protocol):
    """Verifies an agent-authorization bearer token and returns a Principal."""

    def verify(self, bearer_token: str) -> Principal:
        """Return the verified caller identity, or raise ``AuthorizationError``."""
        ...


class JwtIdentityVerifier:
    """Verify a signed OIDC JWT and lift ``iss``/``sub`` into a ``Principal``.

    The signature, issuer, audience and expiry are all checked. ``email`` is read
    only as display metadata; it has no bearing on isolation.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str,
        leeway_seconds: int = 30,
        _jwk_client: PyJWKClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds
        # Injectable for tests; PyJWKClient caches signing keys internally.
        self._jwks = _jwk_client or PyJWKClient(jwks_uri)

    def verify(self, bearer_token: str) -> Principal:
        token = _strip_bearer(bearer_token)
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthorizationError(f"agent authorization rejected: {exc}") from exc

        return Principal(
            issuer=claims["iss"],
            subject=claims["sub"],
            email=claims.get("email"),
            display_name=claims.get("name"),
        )


class DevIdentityVerifier:
    """Insecure verifier for local development and tests only.

    Accepts a token of the form ``"<issuer>|<subject>[|<email>]"``. ``|`` is the
    delimiter (not ``:``) precisely because issuers are URLs that contain ``:``.
    It must be constructed with ``allow_insecure=True`` so it can never be wired
    up in a real deployment without a glaringly obvious opt-in.
    """

    def __init__(self, *, allow_insecure: bool = False) -> None:
        if not allow_insecure:
            raise RuntimeError(
                "DevIdentityVerifier is insecure and must be constructed with "
                "allow_insecure=True; never enable it in a deployed service."
            )

    def verify(self, bearer_token: str) -> Principal:
        token = _strip_bearer(bearer_token)
        parts = token.split("|")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise AuthorizationError("dev token must be '<issuer>|<subject>[|<email>]'")
        issuer, subject = parts[0], parts[1]
        email = parts[2] if len(parts) > 2 and parts[2] else None
        return Principal(issuer=issuer, subject=subject, email=email)


def _strip_bearer(value: str) -> str:
    if not value:
        raise AuthorizationError("missing authorization token")
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[len("bearer ") :].strip()
    if not value:
        raise AuthorizationError("empty authorization token")
    return value
