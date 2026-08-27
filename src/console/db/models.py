import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive_utc(value: datetime) -> datetime:
    """SQLite round-trips datetimes naive while utcnow() is timezone-aware, so
    anything comparing a stored timestamp against now has to flatten both sides
    first or it raises."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, unique=True)
    repo: Mapped[str] = mapped_column(Text, unique=True)  # "owner/repo"
    branch: Mapped[str] = mapped_column(Text, default="main")
    subdomain: Mapped[str] = mapped_column(Text, unique=True)
    # The base domain the app serves under; the host is {subdomain}.{domain}.
    # Null means the primary CONSOLE_DOMAIN (existing projects predate this).
    domain: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Cloudflare Access gate for this app's hostname. protected mirrors whether
    # an Access app exists; cf_app_id is the id we created (to update/delete
    # it); access_emails is the comma-separated allow list.
    protected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    access_emails: Mapped[str | None] = mapped_column(Text)
    cf_app_id: Mapped[str | None] = mapped_column(Text)
    # The app's own favicon, fetched from its running container (never pasted in).
    # Null until a deploy or a manual refresh pulls one; the UI falls back to
    # initials. Stored as raw bytes plus its content type so it serves as-is.
    icon_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    icon_content_type: Mapped[str | None] = mapped_column(Text)
    icon_fetched_at: Mapped[datetime | None]


class AccessPath(Base):
    """One Cloudflare Access bypass: a path on a hostname that machines may
    reach without the browser login, so a Shortcut, a cron job, or a webhook
    sender can call an app's API. Cloudflare matches the more specific path
    first, so the Bypass policy on host/path wins over the login gate on host.

    project_id is null for the console's own hostname, which is not a project.
    hostname is stored rather than derived: a project can move to another
    domain, and the console's own host has no project to derive it from.

    The unique constraint only binds project rows, since SQLite counts NULLs as
    distinct; the console's own paths are deduped in access_paths.add."""

    __tablename__ = "access_paths"
    __table_args__ = (UniqueConstraint("project_id", "path"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    hostname: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    cf_app_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    sha: Mapped[str] = mapped_column(Text)
    commit_message: Mapped[str | None] = mapped_column(Text)
    image: Mapped[str | None] = mapped_column(Text)  # full GHCR ref incl. tag
    status: Mapped[str] = mapped_column(Text)  # queued|building|deploying|live|superseded|failed
    substate: Mapped[str | None] = mapped_column(Text)  # pulling|starting|health_check
    run_url: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    config_snapshot: Mapped[str | None] = mapped_column(Text)  # console.toml as JSON
    container_name: Mapped[str | None] = mapped_column(Text)
    router_priority: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    build_finished_at: Mapped[datetime | None]
    deploy_started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class CommandRun(Base):
    """A one-off command exec'd in a project's live container: app maintenance
    and setup that used to need a hand-rolled `docker exec` (a Robinhood device
    approval, simplefin_setup, a migration, an ad-hoc script). Output is
    appended as it streams so the polling UI sees progress, the same shape as a
    Deployment's log. Never touches a container's lifecycle, so the deploy
    safety invariant is untouched."""

    __tablename__ = "command_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    command: Mapped[str] = mapped_column(Text)
    container_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # running|succeeded|failed
    exit_code: Mapped[int | None]
    output: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class BackupRun(Base):
    """One off-box backup of the console's own state (the SQLite DB plus the
    Fernet key), encrypted with a passphrase kept outside the DB. Recorded like
    a CommandRun so the UI can show history and the last-backup status."""

    __tablename__ = "backup_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    trigger: Mapped[str] = mapped_column(Text)  # scheduled|manual
    status: Mapped[str] = mapped_column(Text)  # running|succeeded|failed
    location: Mapped[str | None] = mapped_column(Text)  # where the bundle landed
    size_bytes: Mapped[int | None]
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class ProjectHealth(Base):
    """Live liveness of a project's app, one row per project, maintained by the
    monitor loop. Distinct from deploy status: an app can be deploy-'live' but
    not answering. alerted tracks whether the current down episode was already
    announced, so an outage alerts once, not every tick."""

    __tablename__ = "project_health"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(Text)  # up|down|unknown
    fail_streak: Mapped[int] = mapped_column(default=0, server_default="0")
    alerted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    checked_at: Mapped[datetime | None]
    changed_at: Mapped[datetime | None]


class Setting(Base):
    """Server-level config the console manages itself: the GHCR read token for
    pulling private images, and the Cloudflare Access credentials. Values are
    Fernet-encrypted with the same key as project secrets."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ApiToken(Base):
    """A credential for the machine-facing /v1 surface: what a script or an AI
    agent presents in place of the browser login the console's own SPA gets from
    Cloudflare Access.

    Only the SHA-256 hash is stored. A token is verified, never read back, so
    unlike a Secret it is not Fernet-encrypted: the console cannot show one
    again after minting, and a stolen database yields nothing usable. preview
    holds the token's first few characters purely so the UI can tell two tokens
    apart; it is far too short to be guessed from."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    preview: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)  # read|write
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None]


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(Text)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
