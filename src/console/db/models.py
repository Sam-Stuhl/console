import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, LargeBinary, Text, UniqueConstraint
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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    build_finished_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


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
