"""Cloudflare Access automation. The console manages ONLY per-app Access
applications: the login gate in front of an app's hostname. It never touches
DNS, the tunnel, or routing (the wildcard route handles those), so the API
token is scoped to "Access: Apps and Policies -> Edit" and that scope is the
whole blast radius.

Optional: without CONSOLE_CF_ACCOUNT_ID and a token at CONSOLE_CF_API_TOKEN_FILE
the access toggle 503s and the rest of the console is unaffected.
"""

from pathlib import Path

import httpx

from console import config


class AccessNotConfigured(Exception):
    def __init__(self) -> None:
        super().__init__(
            "Cloudflare Access is not configured: set CONSOLE_CF_ACCOUNT_ID and put "
            f"a scoped API token at {config.CF_API_TOKEN_FILE} "
            "(permission: Access: Apps and Policies -> Edit)."
        )


class AccessApiError(Exception):
    """A Cloudflare API call failed; carries a readable reason for the UI."""


def _token() -> str:
    try:
        token = Path(config.CF_API_TOKEN_FILE).read_text().strip()
    except OSError as exc:
        raise AccessNotConfigured() from exc
    if not token or not config.CF_ACCOUNT_ID:
        raise AccessNotConfigured()
    return token


def _apps_url() -> str:
    return f"{config.CF_API_BASE}/accounts/{config.CF_ACCOUNT_ID}/access/apps"


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    resp = await client.request(method, url, headers=headers, **kwargs)
    data = resp.json() if resp.content else {}
    if not resp.is_success or not data.get("success", True):
        errors = data.get("errors") or [{"message": resp.text or resp.reason_phrase}]
        reason = "; ".join(e.get("message", str(e)) for e in errors)
        raise AccessApiError(f"Cloudflare API {resp.status_code}: {reason}")
    return data.get("result")


def _policy_body(emails: list[str]) -> dict:
    return {
        "name": "console allow-list",
        "decision": "allow",
        "include": [{"email": {"email": e}} for e in emails],
    }


async def _create_app(client: httpx.AsyncClient, hostname: str, emails: list[str]) -> str:
    app = await _request(
        client,
        "POST",
        _apps_url(),
        json={
            "name": hostname,
            "domain": hostname,
            "type": "self_hosted",
            "session_duration": "24h",
        },
    )
    app_id = app["id"]
    await _request(client, "POST", f"{_apps_url()}/{app_id}/policies", json=_policy_body(emails))
    return app_id


async def _set_policy(client: httpx.AsyncClient, app_id: str, emails: list[str]) -> None:
    policies = await _request(client, "GET", f"{_apps_url()}/{app_id}/policies") or []
    body = _policy_body(emails)
    if policies:
        await _request(client, "PUT", f"{_apps_url()}/{app_id}/policies/{policies[0]['id']}", json=body)
    else:
        await _request(client, "POST", f"{_apps_url()}/{app_id}/policies", json=body)


async def reconcile(
    hostname: str, protected: bool, emails: list[str], cf_app_id: str | None
) -> str | None:
    """Make Cloudflare match the desired state. Returns the Access app id to
    store (str when protected, None when not). Raises AccessNotConfigured (no
    token) or AccessApiError (the API rejected the call)."""
    _token()  # fail fast with a clean 503 when unconfigured
    async with httpx.AsyncClient(timeout=15) as client:
        if not protected:
            if cf_app_id:
                await _request(client, "DELETE", f"{_apps_url()}/{cf_app_id}")
            return None
        if cf_app_id:
            await _set_policy(client, cf_app_id, emails)
            return cf_app_id
        return await _create_app(client, hostname, emails)


async def delete_if_present(cf_app_id: str | None) -> None:
    """Best-effort teardown when a project is deleted. Swallows errors so a
    Cloudflare hiccup never blocks removing the project (at worst it orphans an
    Access app, which you can delete in the dashboard)."""
    if not cf_app_id:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await _request(client, "DELETE", f"{_apps_url()}/{cf_app_id}")
    except (AccessNotConfigured, AccessApiError, httpx.HTTPError):
        pass
