"""Docker SDK access. The SDK is synchronous; every call from async code
must go through run() so the event loop never blocks on the socket."""

import asyncio

import docker

_client: docker.DockerClient | None = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


async def run(fn, /, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)
