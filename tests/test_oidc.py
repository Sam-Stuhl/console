"""Real RS256 round-trips against a generated keypair; only the JWKS fetch
is stubbed, so the signature, issuer, audience, expiry, and owner checks
all run for real."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from console import config, oidc
from console.oidc import OidcError, WrongOwner

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StubSigningKey:
    key = PRIVATE_KEY.public_key()


class StubJwksClient:
    def get_signing_key_from_jwt(self, token):
        return StubSigningKey()


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch):
    monkeypatch.setattr(oidc, "_jwks_client", lambda: StubJwksClient())


def make_token(key=PRIVATE_KEY, **overrides):
    now = int(time.time())
    claims = {
        "iss": config.OIDC_ISSUER,
        "aud": config.OIDC_AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "repository_owner": "sam-stuhl",
        "repository": "sam-stuhl/notion-sync",
        "ref": "refs/heads/main",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def test_valid_token_returns_claims():
    claims = oidc.verify(make_token())
    assert claims["repository"] == "sam-stuhl/notion-sync"
    assert claims["ref"] == "refs/heads/main"


def test_expired_token_rejected():
    now = int(time.time())
    with pytest.raises(OidcError, match="expired"):
        oidc.verify(make_token(iat=now - 600, exp=now - 300))


def test_wrong_audience_rejected():
    with pytest.raises(OidcError, match="[Aa]udience"):
        oidc.verify(make_token(aud="someone-else"))


def test_wrong_issuer_rejected():
    with pytest.raises(OidcError, match="[Ii]ssuer"):
        oidc.verify(make_token(iss="https://evil.example.com"))


def test_missing_exp_rejected():
    with pytest.raises(OidcError):
        oidc.verify(make_token(exp=None))


def test_wrong_signature_rejected():
    with pytest.raises(OidcError):
        oidc.verify(make_token(key=OTHER_KEY))


def test_wrong_owner_rejected():
    with pytest.raises(WrongOwner, match='"not-sam"'):
        oidc.verify(make_token(repository_owner="not-sam"))


def test_missing_owner_rejected():
    with pytest.raises(WrongOwner):
        oidc.verify(make_token(repository_owner=None))


def test_garbage_token_rejected():
    with pytest.raises(OidcError):
        oidc.verify("not-a-jwt-at-all")
