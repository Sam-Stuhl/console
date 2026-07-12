import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, unique=True)
    repo: Mapped[str] = mapped_column(Text, unique=True)  # "sam-stuhl/notion-sync"
    branch: Mapped[str] = mapped_column(Text, default="main")
    subdomain: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Cloudflare Access gate for this app's hostname. protected mirrors whether
    # an Access app exists; cf_app_id is the id we created (to update/delete
    # it); access_emails is the comma-separated allow list.
    protected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    access_emails: Mapped[str | None] = mapped_column(Text)
    cf_app_id: Mapped[str | None] = mapped_column(Text)


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


class Setting(Base):
    """Server-level config the console manages itself: the GHCR read token for
    pulling private images, and the Cloudflare Access credentials. Values are
    Fernet-encrypted with the same key as project secrets."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


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
