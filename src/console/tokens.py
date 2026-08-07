"""Console-issued API tokens: the credential a script or an AI agent presents to
the machine-facing /v1 surface.

Auth inside the app is otherwise a non-goal, because Cloudflare Access at the
edge is the gate for the browser console. A machine client cannot complete an
interactive browser login, so /v1 is the documented exception and these tokens
are the only thing in front of it. That is why the handling here is deliberately
conservative:

- 256 bits of entropy, so guessing one online is not a threat worth rate
  limiting against. Revocation is the answer to a leak.
- Stored as a SHA-256 hash, never encrypted. A token is verified, not read back,
  so the console can show it exactly once and a stolen database yields nothing
  usable. Lookup is by hash (knowing a hash does not get you in, since the
  preimage is what authenticates) and the final check is constant-time.
- The csk_ prefix makes a leaked token greppable in logs and recognizable to
  credential-scanning tools.

Minting and revoking live only behind Cloudflare Access, in the SPA. A token
cannot manage tokens: that would turn one leak into permanent, self-renewing
access."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.db.models import ApiToken, naive_utc, utcnow

PREFIX = "csk_"
ENTROPY_BYTES = 32
PREVIEW_LEN = len(PREFIX) + 8

READ = "read"
WRITE = "write"
SCOPES = (READ, WRITE)

# last_used_at exists so a forgotten token is visible in the UI, not for
# auditing every call, so it is only rewritten once a minute. Without this every
# read request would also be a database write.
TOUCH_INTERVAL = timedelta(seconds=60)


def generate() -> str:
    """A fresh token. This is the only moment its plaintext exists."""
    return PREFIX + secrets.token_urlsafe(ENTROPY_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint(session: AsyncSession, name: str, scope: str) -> tuple[ApiToken, str]:
    """Create a token, returning the row and the plaintext. The caller must show
    the plaintext to the user immediately: it is not recoverable afterwards."""
    if scope not in SCOPES:
        raise ValueError(f'scope must be one of {", ".join(SCOPES)}, not "{scope}"')
    plaintext = generate()
    row = ApiToken(
        name=name,
        token_hash=hash_token(plaintext),
        preview=plaintext[:PREVIEW_LEN],
        scope=scope,
    )
    session.add(row)
    return row, plaintext


async def verify(session: AsyncSession, presented: str) -> ApiToken | None:
    """The token row for a presented string, or None if it does not authenticate.
    Callers must not tell the client which check failed."""
    if not presented or not presented.startswith(PREFIX):
        return None
    digest = hash_token(presented)
    row = await session.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
    if row is None or not hmac.compare_digest(row.token_hash, digest):
        return None

    now = utcnow()
    if (
        row.last_used_at is None
        or naive_utc(now) - naive_utc(row.last_used_at) > TOUCH_INTERVAL
    ):
        row.last_used_at = now
        await session.commit()
    return row


async def list_tokens(session: AsyncSession) -> list[ApiToken]:
    rows = await session.scalars(select(ApiToken).order_by(ApiToken.created_at))
    return list(rows)


async def revoke(session: AsyncSession, token_id: str) -> bool:
    """Delete a token. Revocation is a row delete: a revoked token is simply one
    that no longer exists, so there is no disabled-but-present state to reason
    about."""
    row = await session.get(ApiToken, token_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
