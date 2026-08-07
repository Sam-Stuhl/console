"""Bearer token authentication for /v1.

/v1 is reached through a Cloudflare Access Bypass rule, so unlike every other
path in this app it is exposed to the raw internet and this check is the only
thing in front of it. Two consequences shape the code below:

- Failures say "invalid token" and nothing else. Which check failed (no header,
  wrong scheme, unknown token) is information an attacker can use and a
  legitimate caller does not need.
- Scope is enforced per route, not per token-lookup, so adding a write endpoint
  without saying so is a visible omission rather than a silent hole."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from console import tokens
from console.db.models import ApiToken
from console.db.session import get_session

# auto_error=False so a missing header lands in our own handler and gets the
# same generic answer as a wrong one.
bearer = HTTPBearer(auto_error=False, description="A console API token (csk_…)")

UNAUTHORIZED = "invalid token"
FORBIDDEN = "this token is read-only; a token with write scope is required"


async def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> ApiToken:
    """The authenticated token, or 401. Attached to request.state so a route
    can name the caller in a log line without re-verifying."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail=UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = await tokens.verify(session, credentials.credentials)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail=UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.token = token
    return token


async def require_write(token: ApiToken = Depends(require_token)) -> ApiToken:
    """Same, but refuses a read-only token. Every endpoint that changes
    anything depends on this instead of require_token."""
    if token.scope != tokens.WRITE:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return token
