"""The console's connection to GitHub: connect an account through the OAuth
device flow, see who is connected, disconnect, and list that account's repos.

Everything here is outbound. The token lets the console call GitHub as the
operator; it authenticates nobody to the console, which is still Cloudflare
Access's job alone. These routes sit under /api like any other, behind that
same gate."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from console import github, settings_store
from console.db.session import get_session

router = APIRouter(prefix="/api/github")


class DeviceCode(BaseModel):
    device_code: str


def _api_error(exc: github.GitHubApiError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> dict:
    """Whether a connection is possible, whether one exists, and who it is.
    The login call is also a liveness check: a revoked token reports connected
    with an error rather than silently failing later at the point of use."""
    configured = bool(await github.client_id(session))
    try:
        token = await github.resolve_token(session)
    except github.GitHubNotConnected:
        return {
            "client_configured": configured,
            "connected": False,
            "login": None,
            "error": None,
        }
    login, error = None, None
    try:
        login = await github.GitHub(token).login()
    except github.GitHubApiError as exc:
        error = str(exc)
    return {
        "client_configured": configured,
        "connected": True,
        "login": login,
        "error": error,
    }


@router.post("/device")
async def start_device_flow(session: AsyncSession = Depends(get_session)) -> dict:
    """Begin the flow. The device code comes back to the browser and is sent
    back with each poll, so the console holds no pending state."""
    try:
        return await github.start_device_flow(await github.require_client_id(session))
    except github.DeviceFlowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except github.GitHubApiError as exc:
        raise _api_error(exc)


@router.post("/device/poll")
async def poll_device_flow(
    body: DeviceCode, session: AsyncSession = Depends(get_session)
) -> dict:
    """One poll of a flow in progress. Stores the token once it arrives."""
    try:
        state, token = await github.poll_device_flow(
            await github.require_client_id(session), body.device_code
        )
    except github.DeviceFlowUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except github.GitHubApiError as exc:
        raise _api_error(exc)
    if state == github.CONNECTED and token:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, token)
        await session.commit()
    return {"status": state}


@router.delete("/connection", status_code=204)
async def disconnect(session: AsyncSession = Depends(get_session)) -> None:
    """Forget the token. GitHub still lists the authorization until it is
    revoked there, so the UI says so."""
    await settings_store.delete(session, settings_store.GITHUB_TOKEN)
    await session.commit()


@router.get("/repos")
async def list_repos(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        token = await github.resolve_token(session)
        return {"repos": await github.GitHub(token).list_repos()}
    except github.GitHubNotConnected as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except github.GitHubApiError as exc:
        raise _api_error(exc)


@router.get("/branches")
async def list_branches(
    repo: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Branches in one repo, for the branch picker. The repo rides a query
    parameter rather than the path, since "owner/name" carries a slash."""
    try:
        token = await github.resolve_token(session)
        return {"branches": await github.GitHub(token).list_branches(repo)}
    except github.GitHubNotConnected as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except github.FileNotFound:
        raise HTTPException(status_code=404, detail=f'no repo "{repo}"')
    except github.GitHubApiError as exc:
        raise _api_error(exc)
