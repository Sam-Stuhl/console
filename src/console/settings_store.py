"""Server-level settings the console manages via its own UI: the GHCR read
token for pulling private images, and the Cloudflare Access credentials. Stored
in the settings table, Fernet-encrypted with the same key as project secrets, so
none of it lives in git or a mounted file once set here."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.db.models import Setting
from console.secrets import crypto

GHCR_TOKEN = "ghcr_token"
CF_API_TOKEN = "cf_api_token"
CF_ACCOUNT_ID = "cf_account_id"
# Backup destination: a repo-scoped PAT and the "owner/name" of a private repo
# the encrypted backup bundles are pushed to.
BACKUP_GITHUB_TOKEN = "backup_github_token"
BACKUP_GITHUB_REPO = "backup_github_repo"
# The passphrase that encrypts backup bundles. Storing it here (encrypted like
# any secret) is safe: it only ever appears inside a bundle that is itself
# encrypted with it, so a leaked backup repo still reveals nothing. It is the
# user's job to keep an independent copy (their password manager), because a
# backup exists to survive losing this box. A mounted file
# (CONSOLE_BACKUP_PASSPHRASE_FILE) is still honored as an alternative.
BACKUP_PASSPHRASE = "backup_passphrase"
# ntfy alert channel: the topic the console publishes to (down/up, deploy
# failures) and the server hosting it. The topic name is the only secret; on
# public ntfy.sh anyone who knows it can read the notifications.
NTFY_TOPIC = "ntfy_topic"
NTFY_SERVER = "ntfy_server"

# Everything the settings UI knows how to manage. The account id and repo are
# not really secret, but they ride the same encrypted store for uniformity.
KNOWN = frozenset(
    {
        GHCR_TOKEN,
        CF_API_TOKEN,
        CF_ACCOUNT_ID,
        BACKUP_GITHUB_TOKEN,
        BACKUP_GITHUB_REPO,
        BACKUP_PASSPHRASE,
        NTFY_TOPIC,
        NTFY_SERVER,
    }
)


async def get(session: AsyncSession, key: str) -> str | None:
    row = await session.get(Setting, key)
    return crypto.decrypt(row.value_encrypted) if row is not None else None


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value_encrypted=crypto.encrypt(value)))
    else:
        row.value_encrypted = crypto.encrypt(value)


async def delete(session: AsyncSession, key: str) -> bool:
    row = await session.get(Setting, key)
    if row is None:
        return False
    await session.delete(row)
    return True


async def keys_set(session: AsyncSession) -> set[str]:
    rows = await session.scalars(select(Setting.key))
    return set(rows)
