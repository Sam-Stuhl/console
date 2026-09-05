"""The /v1 response contract.

These are deliberately their own models rather than a reuse of the /api ones.
The /api shapes exist to serve the SPA and change whenever the UI wants a new
field; anything published to outside scripts and agents has to be stable, so
the two are allowed to drift on purpose.

Nothing here can carry a secret value. Secrets appear only as SecretKey, which
is a name and a timestamp."""

from datetime import datetime

from pydantic import BaseModel


class Project(BaseModel):
    id: str
    name: str
    repo: str
    branch: str
    subdomain: str
    domain: str  # the base domain it serves under
    url: str  # where it serves, e.g. https://blog.example.com
    protected: bool  # a Cloudflare Access login sits in front of it
    access_emails: list[str]
    health: str  # live liveness from the monitor: up | down | unknown
    deploy_status: str | None  # latest deployment: queued|building|deploying|live|failed|…
    is_live: bool  # a deployment is serving, independent of the monitor ping
    auto_build: bool  # a push to the tracked branch is built on the box and deployed
    # What CI tags this project's images as, minus the tag: what to prefix a
    # tag with when deploying an image. Derived, never stored.
    image_hint: str
    created_at: datetime


class Deployment(BaseModel):
    id: str
    project_id: str
    sha: str
    commit_message: str | None
    image: str | None
    status: str
    substate: str | None
    run_url: str | None
    failure_reason: str | None
    created_at: datetime
    build_finished_at: datetime | None
    deploy_started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class DeploymentDetail(Deployment):
    log: str | None
    config_snapshot: str | None
    container_name: str | None
    router_priority: int | None


class Container(BaseModel):
    """The app's container. state is "absent" when nothing is running, so a
    caller never has to distinguish a 404 from a stopped app."""

    state: str
    id: str | None = None
    name: str | None = None
    image: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    cpu_percent: float | None = None
    mem_usage: int | None = None
    mem_limit: int | None = None
    mem_percent: float | None = None


class Logs(BaseModel):
    container: str | None
    tail: int
    logs: str


class SecretKey(BaseModel):
    """A secret's existence, never its value. Enough to answer "is DATABASE_URL
    set?" without the API being able to leak what it is."""

    key: str
    updated_at: datetime


class CommandRun(BaseModel):
    id: str
    project_id: str
    command: str
    container_name: str | None
    status: str
    exit_code: int | None
    failure_reason: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class CommandRunDetail(CommandRun):
    output: str | None


class BackupRun(BaseModel):
    id: str
    trigger: str
    status: str
    location: str | None
    size_bytes: int | None
    failure_reason: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class Backups(BaseModel):
    ready: bool
    passphrase: bool
    destination: bool
    runs: list[BackupRun]


class Credential(BaseModel):
    key: str
    label: str
    set: bool
    expires_at: str | None
    days_left: int | None
    state: str  # ok | warn | expired | unknown


class System(BaseModel):
    """One call that says how everything is doing. Meant to be the first thing
    an agent asks for, so it can decide what to look at next."""

    domains: list[str]
    primary_domain: str
    projects: int
    live: int
    down: int
    deploying: int
    credentials: list[Credential]
    backups_ready: bool
    last_backup_at: datetime | None
    last_backup_status: str | None


class Accepted(BaseModel):
    """A write that started background work. The id is pollable via the
    matching read endpoint."""

    id: str
    status: str


class DomainChange(BaseModel):
    project: Project
    redeploy_required: bool
    note: str | None = None


class AccessPath(BaseModel):
    """One path that skips the Cloudflare Access login, so a script, a
    Shortcut, or a webhook sender can reach it."""

    id: str
    project_id: str | None  # null means the console's own hostname
    hostname: str
    path: str  # no leading slash, e.g. "api/ingest"
    url: str  # what a caller hits, e.g. https://app.example.com/api/ingest
    created_at: datetime


class AccessPathList(BaseModel):
    # The hostname comes back even when the list is empty: it is what the paths
    # are relative to, and for the console's own scope nothing else states it.
    hostname: str
    paths: list[AccessPath]
