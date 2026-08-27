"""Which policy the access toggle rewrites.

An Access app can carry policies the console did not write: a Cloudflare service
token is the common one, and it is how a machine reaches an app that is
otherwise gated. Rewriting one of those into an email allow-list would revoke it
silently, so the console finds its own by name."""

import pytest

from console import cloudflare

CONSOLE = {"id": "pol-console", "name": cloudflare.Access.POLICY_NAME, "decision": "allow"}
FOREIGN = {"id": "pol-token", "name": "claude-mcp-service-token", "decision": "non_identity"}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.content = b"{}"
        self.text = ""
        self.reason_phrase = ""

    @property
    def is_success(self):
        return True

    def json(self):
        return self._payload


class FakeClient:
    """httpx.AsyncClient inside console.cloudflare, answering with a fixed set
    of policies and recording every write."""

    policies: list = []
    sent: list = []

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def request(self, method, url, **kwargs):
        FakeClient.sent.append((method, url, kwargs.get("json")))
        if url.endswith("/policies") and method == "GET":
            return FakeResponse({"success": True, "result": FakeClient.policies})
        return FakeResponse({"success": True, "result": {"id": "x"}})


@pytest.fixture
def cf_http(monkeypatch):
    FakeClient.policies = []
    FakeClient.sent = []
    monkeypatch.setattr(cloudflare.httpx, "AsyncClient", FakeClient)
    return FakeClient


def writes(sent):
    return [(m, u, b) for m, u, b in sent if m in ("PUT", "POST")]


async def test_the_console_rewrites_its_own_policy_not_the_first_one(cf_http):
    # The case that matters: something else sorts above the console's policy.
    cf_http.policies = [FOREIGN, CONSOLE]

    await cloudflare.Access("tok", "acct").reconcile(
        "money.example.com", True, ["owner@example.com"], "app-1"
    )

    (method, url, body), = writes(cf_http.sent)
    assert method == "PUT"
    assert url.endswith("/policies/pol-console")  # not pol-token
    assert body["decision"] == "allow"


async def test_the_ordinary_case_still_updates_in_place(cf_http):
    cf_http.policies = [CONSOLE]

    await cloudflare.Access("tok", "acct").reconcile(
        "app.example.com", True, ["owner@example.com"], "app-1"
    )

    (method, url, _body), = writes(cf_http.sent)
    assert method == "PUT" and url.endswith("/policies/pol-console")


async def test_a_missing_console_policy_is_created_not_hijacked(cf_http):
    # Somebody deleted the console's policy but left their own. Adding ours back
    # is right; taking theirs over is not.
    cf_http.policies = [FOREIGN]

    await cloudflare.Access("tok", "acct").reconcile(
        "app.example.com", True, ["owner@example.com"], "app-1"
    )

    (method, url, body), = writes(cf_http.sent)
    assert method == "POST" and url.endswith("/policies")
    assert body["name"] == cloudflare.Access.POLICY_NAME
