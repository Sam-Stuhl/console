"""GitHub Actions OIDC token verification. The webhook endpoints trust a
request only if it carries a JWT signed by GitHub's Actions issuer, minted
for this console (audience) and for a repository Sam owns. No shared
secrets anywhere; the app repos' workflows request the token with
audience=console and send it as a bearer header.

verify() does blocking network I/O on a JWKS cache miss; call it via
asyncio.to_thread from async code."""

import jwt
from jwt import PyJWKClient

from console import config


class OidcError(Exception):
    """Token failed verification. The message is safe to return as detail."""


class WrongOwner(OidcError):
    """Valid GitHub token, but not for a sam-stuhl repository."""


_jwks: PyJWKClient | None = None


def _jwks_client() -> PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = PyJWKClient(config.OIDC_JWKS_URL, cache_keys=True)
    return _jwks


def verify(token: str) -> dict:
    """Return the verified claims or raise OidcError/WrongOwner."""
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=config.OIDC_ISSUER,
            audience=config.OIDC_AUDIENCE,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise OidcError(f"OIDC token rejected: {exc}") from exc

    # GitHub emits the owner in its canonical case (e.g. "Sam-Stuhl"), but
    # GitHub identifiers are case-insensitive, so compare that way.
    owner = claims.get("repository_owner") or ""
    if owner.lower() != config.OIDC_OWNER.lower():
        raise WrongOwner(
            f'OIDC token rejected: repository_owner is "{owner}", '
            f'not "{config.OIDC_OWNER}"'
        )
    return claims
