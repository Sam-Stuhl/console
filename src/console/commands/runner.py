"""Runs a one-off command in a project's live container via `docker exec`,
streaming its output into the command_runs row so the polling UI sees progress.

This mirrors the deploy engine's shape (a task set + enqueue, and an
append-and-commit log helper) but is far simpler: it never pulls, runs, stops,
or removes a container, so the deploy safety invariant does not apply here.

Two known limitations, both acceptable for a single-tenant console:
- A redeploy that swaps the container mid-command removes the old container and
  the exec stream ends; the run records whatever exit the daemon reports.
- On COMMAND_TIMEOUT the row goes terminal and the UI is unblocked, but the
  process keeps running inside the container: an exec has no clean SDK kill.
Interactive stdin (typing an SMS/2FA code) is a deliberate non-feature; it would
hook in at exec_create (stdin=True, tty=True) plus a socket write path."""

import asyncio
import time

from sqlalchemy.ext.asyncio import AsyncSession

from console import config
from console.db.models import CommandRun, utcnow
from console.db.session import SessionLocal
from console.docker.client import get_client, run
from console.docker.containers import find_project_container

_tasks: set[asyncio.Task] = set()

_TRUNCATED = "\n... output truncated ...\n"


class CommandTimeout(Exception):
    """The command outran COMMAND_TIMEOUT."""


def enqueue(run_id: str) -> None:
    """Spawn a task to run a queued command."""
    task = asyncio.create_task(run_command(run_id))
    _tasks.add(task)  # strong ref; asyncio only keeps weak ones
    task.add_done_callback(_tasks.discard)


async def run_command(run_id: str) -> None:
    async with SessionLocal() as session:
        cmd_run = await session.get(CommandRun, run_id)
        if cmd_run is None:
            return
        try:
            await _exec(session, cmd_run)
        except CommandTimeout:
            await _finish(
                session,
                cmd_run,
                status="failed",
                reason=f"timed out after {config.COMMAND_TIMEOUT // 60} minutes",
            )
        except Exception as exc:  # tasks must never die silently
            await _finish(
                session, cmd_run, status="failed", reason=f"internal error: {exc!r}"
            )


async def _exec(session: AsyncSession, cmd_run: CommandRun) -> None:
    container = await find_project_container(cmd_run.project_id)
    if container is None:
        await _finish(
            session,
            cmd_run,
            status="failed",
            reason="app is not running; deploy it first",
        )
        return
    cmd_run.container_name = container.name
    await session.commit()

    client = get_client()
    # A shell line, not an argv, so pipes and env expansion work as typed.
    created = await run(
        client.api.exec_create,
        container.name,
        ["/bin/sh", "-lc", cmd_run.command],
        stdout=True,
        stderr=True,
        stdin=False,
        tty=False,
    )
    exec_id = created["Id"]
    stream = await run(client.api.exec_start, exec_id, stream=True)

    deadline = time.monotonic() + config.COMMAND_TIMEOUT
    truncated = False
    while True:
        chunk = await asyncio.to_thread(next, stream, None)
        if chunk is None:
            break
        if not truncated:
            truncated = await _append(
                session, cmd_run, chunk.decode("utf-8", errors="replace")
            )
        if time.monotonic() > deadline:
            raise CommandTimeout
    # Drained cleanly; the exit code is available now.
    info = await run(client.api.exec_inspect, exec_id)
    exit_code = info.get("ExitCode")
    cmd_run.exit_code = exit_code
    await _finish(
        session,
        cmd_run,
        status="succeeded" if exit_code == 0 else "failed",
        reason=None if exit_code == 0 else f"exited {exit_code}",
    )


async def _append(session: AsyncSession, cmd_run: CommandRun, text: str) -> bool:
    """Append output and commit so the poller sees progress. Returns True once
    the stored output has hit COMMAND_OUTPUT_MAX (the caller stops appending
    but keeps draining so the exit code is still captured)."""
    current = cmd_run.output or ""
    room = config.COMMAND_OUTPUT_MAX - len(current)
    if room <= 0:
        return True
    if len(text) > room:
        cmd_run.output = current + text[:room] + _TRUNCATED
        await session.commit()
        return True
    cmd_run.output = current + text
    await session.commit()
    return False


async def _finish(
    session: AsyncSession, cmd_run: CommandRun, status: str, reason: str | None
) -> None:
    cmd_run.status = status
    cmd_run.failure_reason = reason
    cmd_run.finished_at = utcnow()
    await session.commit()
