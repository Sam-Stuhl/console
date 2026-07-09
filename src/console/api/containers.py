import docker.errors
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from console import config
from console.docker import containers

router = APIRouter(prefix="/api/containers")


@router.get("")
async def list_containers() -> list[dict]:
    return await containers.list_containers()


@router.get("/{container_id}")
async def get_container(container_id: str) -> dict:
    try:
        return await containers.get_container(container_id)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="no such container")


@router.get("/{container_id}/stats")
async def get_stats(container_id: str) -> dict:
    try:
        return await containers.get_stats(container_id)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="no such container")


@router.get("/{container_id}/logs", response_class=PlainTextResponse)
async def get_logs(container_id: str, tail: int = config.LOG_TAIL_DEFAULT) -> str:
    try:
        return await containers.get_logs(container_id, tail)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="no such container")
