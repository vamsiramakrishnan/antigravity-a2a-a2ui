from __future__ import annotations

import datetime

import jwt
import pytest

from a2a_workspace.config import WORKSPACE_NAMESPACE
from a2a_workspace.errors import AuthorizationError
from a2a_workspace.identity.principal import Principal
from a2a_workspace.identity.verifier import DevIdentityVerifier, JwtIdentityVerifier


def test_principal_key_is_from_issuer_and_subject_not_email():
    a = Principal(issuer="https://idp", subject="user-123", email="a@x.com")
    b = Principal(issuer="https://idp", subject="user-123", email="renamed@x.com")
    # Email differs but the identity (and isolation key) is the same.
    assert a == b
    assert a.key == b.key


def test_principal_key_changes_with_subject():
    a = Principal(issuer="https://idp", subject="user-123")
    b = Principal(issuer="https://idp", subject="user-999")
    assert a.key != b.key


def test_workspace_id_is_deterministic_uuid():
    p = Principal(issuer="https://idp", subject="user-123")
    one = p.derive_workspace_id(namespace=WORKSPACE_NAMESPACE)
    two = p.derive_workspace_id(namespace=WORKSPACE_NAMESPACE)
    assert one == two
    assert len(one) == 36  # uuid string


def test_principal_rejects_empty_or_whitespace():
    with pytest.raises(ValueError):
        Principal(issuer="", subject="x")
    with pytest.raises(ValueError):
        Principal(issuer="has space", subject="x")


def test_principal_repr_redacts_subject():
    p = Principal(issuer="https://idp", subject="secret-sub")
    assert "secret-sub" not in repr(p)


def test_dev_verifier_requires_explicit_insecure_flag():
    with pytest.raises(RuntimeError):
        DevIdentityVerifier()
    v = DevIdentityVerifier(allow_insecure=True)
    p = v.verify("https://idp|user-1|user@x.com")
    assert p.issuer == "https://idp"
    assert p.subject == "user-1"
    assert p.email == "user@x.com"


def test_dev_verifier_rejects_malformed_token():
    v = DevIdentityVerifier(allow_insecure=True)
    with pytest.raises(AuthorizationError):
        v.verify("no-delimiter")


def test_jwt_verifier_accepts_valid_token_and_rejects_bad_audience():
    # Build an RSA keypair and a fake JWKS client that returns the public key.
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeSigningKey:
        def __init__(self, public_key):
            self.key = public_key

    class FakeJwks:
        def __init__(self, public_key):
            self._pk = public_key

        def get_signing_key_from_jwt(self, token):
            return FakeSigningKey(self._pk)

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    claims = {
        "iss": "https://idp",
        "sub": "user-123",
        "aud": "my-agent",
        "email": "u@x.com",
        "name": "U",
        "exp": now + datetime.timedelta(minutes=5),
        "iat": now,
    }
    token = jwt.encode(claims, key, algorithm="RS256")

    verifier = JwtIdentityVerifier(
        issuer="https://idp",
        audience="my-agent",
        jwks_uri="https://idp/jwks",
        _jwk_client=FakeJwks(key.public_key()),
    )
    principal = verifier.verify(f"Bearer {token}")
    assert principal.subject == "user-123"
    assert principal.email == "u@x.com"

    wrong = JwtIdentityVerifier(
        issuer="https://idp",
        audience="someone-else",
        jwks_uri="https://idp/jwks",
        _jwk_client=FakeJwks(key.public_key()),
    )
    with pytest.raises(AuthorizationError):
        wrong.verify(token)
