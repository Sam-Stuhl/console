"""The token store behind the machine-facing /v1 surface. These tests exist
because this is the only credential standing in front of an internet-facing
path, so the properties that matter are: the plaintext is never stored, a
near-miss never authenticates, and revoking really removes it."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from console import tokens
from console.db.models import ApiToken, naive_utc, utcnow


async def test_mint_returns_a_prefixed_token_and_stores_only_its_hash(db):
    async with db() as session:
        row, plaintext = await tokens.mint(session, "laptop", tokens.READ)
        await session.commit()

        assert plaintext.startswith(tokens.PREFIX)
        assert row.token_hash == tokens.hash_token(plaintext)
        assert row.preview == plaintext[: tokens.PREVIEW_LEN]

    # The plaintext must appear nowhere in the row that was persisted.
    async with db() as session:
        stored = await session.scalar(select(ApiToken))
        assert plaintext not in (stored.token_hash, stored.name, stored.scope)
        assert stored.preview in plaintext  # a fragment for display, not the token


async def test_every_token_is_distinct():
    assert tokens.generate() != tokens.generate()


async def test_verify_accepts_the_real_token(db):
    async with db() as session:
        _, plaintext = await tokens.mint(session, "agent", tokens.WRITE)
        await session.commit()

    async with db() as session:
        row = await tokens.verify(session, plaintext)
        assert row is not None
        assert row.scope == tokens.WRITE


@pytest.mark.parametrize(
    "mangle",
    [
        lambda t: t[:-1],  # truncated
        lambda t: t + "x",  # extended
        lambda t: t[:-1] + ("a" if t[-1] != "a" else "b"),  # last character off
        lambda t: t.replace(tokens.PREFIX, "", 1),  # prefix stripped
        lambda t: t.upper(),  # case flipped
        lambda _: "",
        lambda _: "not-a-token",
    ],
)
async def test_verify_rejects_a_near_miss(db, mangle):
    async with db() as session:
        _, plaintext = await tokens.mint(session, "agent", tokens.READ)
        await session.commit()

    async with db() as session:
        assert await tokens.verify(session, mangle(plaintext)) is None


async def test_verify_rejects_a_token_from_an_empty_store(db):
    async with db() as session:
        assert await tokens.verify(session, tokens.generate()) is None


async def test_verify_records_last_used(db):
    async with db() as session:
        row, plaintext = await tokens.mint(session, "agent", tokens.READ)
        await session.commit()
        assert row.last_used_at is None

    async with db() as session:
        used = await tokens.verify(session, plaintext)
        assert used.last_used_at is not None


async def test_last_used_is_not_rewritten_on_every_call(db):
    """A read-only request must not cost a database write each time."""
    async with db() as session:
        _, plaintext = await tokens.mint(session, "agent", tokens.READ)
        await session.commit()

    async with db() as session:
        first = (await tokens.verify(session, plaintext)).last_used_at
    async with db() as session:
        second = (await tokens.verify(session, plaintext)).last_used_at
    # Compared through naive_utc because the first value is still the aware one
    # this process assigned, while the second has been round-tripped by SQLite.
    assert naive_utc(first) == naive_utc(second)


async def test_last_used_is_rewritten_once_the_interval_passes(db):
    async with db() as session:
        row, plaintext = await tokens.mint(session, "agent", tokens.READ)
        row.last_used_at = utcnow() - tokens.TOUCH_INTERVAL - timedelta(seconds=1)
        await session.commit()
        stale = row.last_used_at

    async with db() as session:
        assert (await tokens.verify(session, plaintext)).last_used_at != stale


async def test_mint_rejects_an_unknown_scope(db):
    async with db() as session:
        with pytest.raises(ValueError, match="scope must be one of"):
            await tokens.mint(session, "agent", "admin")


async def test_revoke_removes_the_token(db):
    async with db() as session:
        row, plaintext = await tokens.mint(session, "agent", tokens.READ)
        await session.commit()
        token_id = row.id

    async with db() as session:
        assert await tokens.revoke(session, token_id) is True
    async with db() as session:
        assert await tokens.verify(session, plaintext) is None
        assert await tokens.list_tokens(session) == []


async def test_revoke_is_false_for_an_unknown_id(db):
    async with db() as session:
        assert await tokens.revoke(session, "nope") is False


async def test_list_returns_tokens_in_creation_order(db):
    async with db() as session:
        await tokens.mint(session, "first", tokens.READ)
        await tokens.mint(session, "second", tokens.WRITE)
        await session.commit()

    async with db() as session:
        assert [t.name for t in await tokens.list_tokens(session)] == ["first", "second"]
