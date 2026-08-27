"""Cloudflare Access automation. The console manages ONLY Access applications:
the login gate in front of a hostname, and the Bypass apps that let machines
reach one path through it. It never touches DNS, the tunnel, or routing (the
wildcard route handles those), so the API token is scoped to "Access: Apps and
Policies -> Edit" and that scope is the whole blast radius.

Credentials come from Settings (managed in the console UI), falling back to the
CONSOLE_CF_* file/env for backward compatibility. Without either, the access
toggle 503s and the rest of the console is unaffected.
"""

from contextlib import suppress
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, settings_store


class AccessNotConfigured(Exception):
    def __init__(self) -> None:
        super().__init__(
            "Cloudflare Access is not configured. Add a Cloudflare API token "
            "(scoped to Access: Apps and Policies) and your account id in Settings."
        )


class AccessApiError(Exception):
    """A Cloudflare API call failed; carries a readable reason for the UI."""


def _file_token() -> str | None:
    try:
        token = Path(config.CF_API_TOKEN_FILE).read_text().strip()
    except OSError:
        return None
    return token or None


async def resolve_credentials(session: AsyncSession) -> tuple[str, str]:
    """Token + account id from Settings, falling back to the mounted file / env.
    Raises AccessNotConfigured if either is missing."""
    token = await settings_store.get(session, settings_store.CF_API_TOKEN) or _file_token()
    account_id = (
        await settings_store.get(session, settings_store.CF_ACCOUNT_ID) or config.CF_ACCOUNT_ID
    )
    if not token or not account_id:
        raise AccessNotConfigured()
    return token, account_id


class Access:
    """Cloudflare Access app management for one account."""

    def __init__(self, token: str, account_id: str) -> None:
        self._token = token
        self._account_id = account_id

    def _apps_url(self) -> str:
        return f"{config.CF_API_BASE}/accounts/{self._account_id}/access/apps"

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kwargs):
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        resp = await client.request(method, url, headers=headers, **kwargs)
        data = resp.json() if resp.content else {}
        if not resp.is_success or not data.get("success", True):
            errors = data.get("errors") or [{"message": resp.text or resp.reason_phrase}]
            reason = "; ".join(e.get("message", str(e)) for e in errors)
            raise AccessApiError(f"Cloudflare API {resp.status_code}: {reason}")
        return data.get("result")

    @staticmethod
    def _policy_body(emails: list[str]) -> dict:
        return {
            "name": "console allow-list",
            "decision": "allow",
            "include": [{"email": {"email": e}} for e in emails],
        }

    @staticmethod
    def _bypass_body() -> dict:
        """Let everyone through with no login. Only ever attached to an app
        scoped to a single path, whose own authentication is then the only
        thing in front of it."""
        return {
            "name": "console bypass",
            "decision": "bypass",
            "include": [{"everyone": {}}],
        }

    async def _create_app(self, client: httpx.AsyncClient, hostname: str, emails: list[str]) -> str:
        app = await self._request(
            client,
            "POST",
            self._apps_url(),
            json={
                "name": hostname,
                "domain": hostname,
                "type": "self_hosted",
                "session_duration": "24h",
            },
        )
        app_id = app["id"]
        await self._request(
            client, "POST", f"{self._apps_url()}/{app_id}/policies", json=self._policy_body(emails)
        )
        return app_id

    async def _set_policy(self, client: httpx.AsyncClient, app_id: str, emails: list[str]) -> None:
        policies = await self._request(client, "GET", f"{self._apps_url()}/{app_id}/policies") or []
        body = self._policy_body(emails)
        if policies:
            await self._request(
                client, "PUT", f"{self._apps_url()}/{app_id}/policies/{policies[0]['id']}", json=body
            )
        else:
            await self._request(client, "POST", f"{self._apps_url()}/{app_id}/policies", json=body)

    async def reconcile(
        self, hostname: str, protected: bool, emails: list[str], cf_app_id: str | None
    ) -> str | None:
        """Make Cloudflare match the desired state. Returns the Access app id to
        store (str when protected, None when not). Raises AccessApiError on a
        rejected call."""
        async with httpx.AsyncClient(timeout=15) as client:
            if not protected:
                if cf_app_id:
                    await self._request(client, "DELETE", f"{self._apps_url()}/{cf_app_id}")
                return None
            if cf_app_id:
                await self._set_policy(client, cf_app_id, emails)
                return cf_app_id
            return await self._create_app(client, hostname, emails)

    async def create_bypass(self, hostname: str, path: str) -> str:
        """Register {hostname}/{path} as its own Access app that lets everyone
        through. Cloudflare evaluates the more specific path first, so this
        wins over any login gate on the bare hostname.

        An app whose policy failed to attach would deny everyone rather than
        bypass, which is the opposite of what was asked for, so a half-created
        app is removed before the error is raised."""
        async with httpx.AsyncClient(timeout=15) as client:
            app = await self._request(
                client,
                "POST",
                self._apps_url(),
                json={
                    "name": f"{hostname}/{path} (bypass)",
                    "domain": f"{hostname}/{path}",
                    "type": "self_hosted",
                    "session_duration": "24h",
                },
            )
            app_id = app["id"]
            try:
                await self._request(
                    client,
                    "POST",
                    f"{self._apps_url()}/{app_id}/policies",
                    json=self._bypass_body(),
                )
            except AccessApiError:
                with suppress(Exception):
                    await self._request(client, "DELETE", f"{self._apps_url()}/{app_id}")
                raise
            return app_id

    async def delete_app(self, cf_app_id: str) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            await self._request(client, "DELETE", f"{self._apps_url()}/{cf_app_id}")
