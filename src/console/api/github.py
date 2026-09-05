"""The console's connection to GitHub: authorize an account, see who is
connected, disconnect, and list that account's repos and branches.

Everything here is outbound. The token lets the console call GitHub as the
operator; it authenticates nobody to the console, which is still Cloudflare
Access's job alone. These routes sit under /api like any other, behind that
same gate, and that includes the callback: GitHub redirects the operator's
BROWSER to it, so it arrives carrying their Access session. Nothing here needs
an Access bypass: nothing GitHub's servers call reaches this console.

The connection is a plain authorization-code redirect. The console hands the
browser to GitHub, GitHub hands it back with a code, and the console trades the
code for a token there and then, so a connection either exists or visibly
failed. The two halves are tied together by a state value held in a short-lived
cookie: the cookie is the only thing the console keeps between them, so there
is still no server-side session."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, github, settings_store
from console.db.session import get_session

router = APIRouter(prefix="/api/github")

STATE_COOKIE = "console_github_state"
SETTINGS_URL = "/settings"


def _api_error(exc: github.GitHubApiError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


def _callback_url(request: Request) -> str:
    """Where GitHub sends the browser back. Derived from the request, so it
    matches whatever hostname the console is actually being used on, and the
    same value is sent to both ends of the exchange (GitHub requires that)."""
    return str(request.url_for("github_callback"))


def _back_to_settings(outcome: str, detail: str | None = None) -> RedirectResponse:
    """Land the operator back where they started, with the result in the URL so
    the page can say plainly whether it worked."""
    url = f"{SETTINGS_URL}?github={outcome}"
    if detail:
        url += f"&detail={detail}"
    response = RedirectResponse(url, status_code=303)
    response.delete_cookie(STATE_COOKIE)
    return response


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> dict:
    """Whether a connection is possible, whether one exists, and who it is.
    The login call is also a liveness check: a revoked token reports connected
    with an error rather than silently failing later at the point of use."""
    configured = True
    try:
        await github.app_credentials(session)
    except github.NotSetUp:
        configured = False
    try:
        token = await github.resolve_token(session)
    except github.GitHubNotConnected:
        return {
            "app_configured": configured,
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
        "app_configured": configured,
        "connected": True,
        "login": login,
        "error": error,
    }


@router.get("/authorize")
async def authorize(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Send the operator to GitHub to approve the console."""
    try:
        identifier, _ = await github.app_credentials(session)
    except github.NotSetUp as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        github.authorize_url(identifier, _callback_url(request), state),
        status_code=303,
    )
    # The state's only job is to prove the callback belongs to this request.
    # Short-lived, http-only, and same-site lax so it survives the return trip
    # from github.com.
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=config.GITHUB_STATE_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/api/github",
    )
    return response


@router.get("/callback", name="github_callback")
async def callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Where GitHub returns the operator. Trades the code for a token and
    stores it, so by the time the page reloads the answer is already known."""
    if error:
        # The operator cancelled, or GitHub refused the app outright.
        return _back_to_settings("failed", error)

    expected = request.cookies.get(STATE_COOKIE)
    if not state or not expected or not secrets.compare_digest(state, expected):
        # A callback that did not come from an authorize this browser started.
        return _back_to_settings("failed", "state_mismatch")
    if not code:
        return _back_to_settings("failed", "no_code")

    try:
        identifier, secret = await github.app_credentials(session)
        token = await github.exchange_code(
            identifier, secret, code, _callback_url(request)
        )
    except github.NotSetUp:
        return _back_to_settings("failed", "not_set_up")
    except github.GitHubApiError:
        return _back_to_settings("failed", "exchange_failed")

    await settings_store.set_value(session, settings_store.GITHUB_TOKEN, token)
    await session.commit()
    return _back_to_settings("connected")


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
