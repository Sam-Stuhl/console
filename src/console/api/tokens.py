"""Mint and revoke the API tokens that authenticate the machine-facing /v1
surface.

This lives under /api, behind Cloudflare Access, deliberately: a token must not
be able to mint another token, or one leak becomes permanent, self-renewing
access. Creating and revoking is something a human does in the browser.

Like the settings API, this is write-only about the sensitive part. Creation is
the one and only time a token's plaintext is returned; afterwards the console
holds nothing but a hash and can never show it again."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from console import tokens
from console.db.models import ApiToken
from console.db.session import get_session

router = APIRouter(prefix="/api/tokens")


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scope: str = tokens.READ


class TokenOut(BaseModel):
    """A token's metadata. Never carries anything that could authenticate."""

    id: str
    name: str
    preview: str  # the first few characters, so two tokens can be told apart
    scope: str
    created_at: datetime
    last_used_at: datetime | None


class TokenCreated(TokenOut):
    token: str  # shown once, at creation, and never recoverable afterwards


def _out(row: ApiToken) -> TokenOut:
    return TokenOut(
        id=row.id,
        name=row.name,
        preview=row.preview,
        scope=row.scope,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.get("")
async def list_api_tokens(
    session: AsyncSession = Depends(get_session),
) -> list[TokenOut]:
    return [_out(row) for row in await tokens.list_tokens(session)]


@router.post("", status_code=201)
async def create_api_token(
    body: TokenCreate, session: AsyncSession = Depends(get_session)
) -> TokenCreated:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="give the token a name")
    try:
        row, plaintext = await tokens.mint(session, name, body.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await session.commit()
    return TokenCreated(**_out(row).model_dump(), token=plaintext)


@router.delete("/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    if not await tokens.revoke(session, token_id):
        raise HTTPException(status_code=404, detail="no such token")
