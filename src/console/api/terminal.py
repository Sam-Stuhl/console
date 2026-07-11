"""An interactive shell into a project's live container, over a websocket.

This is the one long-lived connection in an otherwise poll-only app (see the
scope note in CLAUDE.md): a real TTY cannot be delivered by polling. The console
opens a TTY `docker exec` in the running container and bridges raw bytes between
it and the browser's xterm terminal. App repos are untouched, so every deployed
app gets a shell for free.

Wire protocol on the socket:
- browser -> console: binary frames are keystrokes (written to the exec's
  stdin); text frames are JSON control messages, currently {"resize": {...}}.
- console -> browser: binary frames are raw terminal output.

Auth is the same as everything else in the console: Cloudflare Access at the
edge, nothing in the app."""

import asyncio
import json
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from console.docker.client import get_client, run
from console.docker.containers import find_project_container

router = APIRouter()


@router.websocket("/api/projects/{project_id}/terminal")
async def terminal(ws: WebSocket, project_id: str) -> None:
    await ws.accept()

    container = await find_project_container(project_id)
    if container is None:
        await ws.send_text("\r\n\x1b[31mapp is not running; deploy it first\x1b[0m\r\n")
        await ws.close()
        return

    client = get_client()
    # Prefer bash for a nicer shell, fall back to sh; TERM lets apps emit color.
    created = await run(
        client.api.exec_create,
        container.id,
        # A failed `exec` is fatal in sh, so probe for bash before execing it.
        ["/bin/sh", "-c", "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi"],
        tty=True,
        stdin=True,
        stdout=True,
        stderr=True,
        environment=["TERM=xterm-256color"],
    )
    exec_id = created["Id"]
    sock = await run(client.api.exec_start, exec_id, tty=True, socket=True, detach=False)
    raw = getattr(sock, "_sock", sock)
    raw.setblocking(True)

    async def pump_out() -> None:
        # container -> browser
        while True:
            data = await asyncio.to_thread(raw.recv, 4096)
            if not data:  # the shell exited and closed its side
                break
            await ws.send_bytes(data)

    async def pump_in() -> None:
        # browser -> container: keystrokes (binary) and resize control (text)
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (data := message.get("bytes")) is not None:
                await asyncio.to_thread(raw.sendall, data)
            elif (text := message.get("text")) is not None:
                await _handle_control(client, exec_id, text)

    out = asyncio.create_task(pump_out())
    inp = asyncio.create_task(pump_in())
    try:
        await asyncio.wait({out, inp}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in (out, inp):
            task.cancel()
        with suppress(Exception):
            raw.close()  # unblocks a pump_out recv still waiting in its thread
        with suppress(Exception):
            await ws.close()


async def _handle_control(client, exec_id: str, text: str) -> None:
    try:
        control = json.loads(text)
    except ValueError:
        return
    size = control.get("resize")
    if size:
        with suppress(Exception):
            await run(
                client.api.exec_resize,
                exec_id,
                height=int(size["rows"]),
                width=int(size["cols"]),
            )
