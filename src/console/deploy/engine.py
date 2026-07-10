"""Deploy orchestration. The hooks call enqueue(); everything else is
internal."""


def enqueue(deployment_id: str) -> None:
    """Spawn a deploy task for a queued deployment. Lands with the engine
    in the next commit; until then nothing may call this outside tests."""
    raise NotImplementedError("deploy engine not wired yet")
