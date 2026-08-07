import base64

import httpx
import pytest

from conftest import GITHUB_CLIENT_ID, FakeResponse
from console import config, github, settings_store


@pytest.fixture(autouse=True)
def http(github_http):
    """Every test here talks to the stubbed GitHub."""
    return github_http


# --- device flow -----------------------------------------------------------


async def test_start_device_flow_returns_what_the_browser_needs(http):
    http.routes["login/device/code"] = FakeResponse(
        {
            "device_code": "dev-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 7,
            "expires_in": 900,
        }
    )

    result = await github.start_device_flow()

    assert result["device_code"] == "dev-123"
    assert result["user_code"] == "ABCD-1234"
    assert result["interval"] == 7
    _, _, kwargs = http.requests[0]
    assert kwargs["data"]["client_id"] == GITHUB_CLIENT_ID
    assert kwargs["data"]["scope"] == config.GITHUB_SCOPE


async def test_start_device_flow_without_a_client_id_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")
    with pytest.raises(github.DeviceFlowUnavailable):
        await github.start_device_flow()


@pytest.mark.parametrize(
    "error,expected",
    [
        ("authorization_pending", github.PENDING),
        ("slow_down", github.PENDING),
        ("access_denied", github.DENIED),
        ("expired_token", github.EXPIRED),
    ],
)
async def test_poll_maps_github_errors_to_states(http, error, expected):
    http.routes["login/oauth/access_token"] = FakeResponse({"error": error})

    state, token = await github.poll_device_flow("dev-123")

    assert (state, token) == (expected, None)


async def test_poll_returns_the_token_once_approved(http):
    http.routes["login/oauth/access_token"] = FakeResponse({"access_token": "gho_abc"})

    state, token = await github.poll_device_flow("dev-123")

    assert (state, token) == (github.CONNECTED, "gho_abc")


async def test_poll_raises_on_an_error_it_does_not_know(http):
    http.routes["login/oauth/access_token"] = FakeResponse(
        {"error": "incorrect_client_credentials", "error_description": "bad id"}
    )

    with pytest.raises(github.GitHubApiError, match="bad id"):
        await github.poll_device_flow("dev-123")


async def test_unreachable_github_is_a_readable_error(http):
    http.routes["login/device/code"] = httpx.ConnectError("dns")

    with pytest.raises(github.GitHubApiError, match="could not reach GitHub"):
        await github.start_device_flow()


# --- token storage ---------------------------------------------------------


async def test_resolve_token_reports_not_connected_when_unset(db):
    async with db() as session:
        with pytest.raises(github.GitHubNotConnected):
            await github.resolve_token(session)


async def test_resolve_token_reads_the_stored_token(db):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gho_abc")
        await session.commit()
        assert await github.resolve_token(session) == "gho_abc"


# --- repo listing ----------------------------------------------------------


def _repo(full_name, default_branch="main", private=False):
    return {"full_name": full_name, "default_branch": default_branch, "private": private}


async def test_list_repos_keeps_only_the_configured_owner(http, monkeypatch):
    monkeypatch.setattr(config, "OIDC_OWNER", "Example-Owner")
    http.routes["/user/repos"] = FakeResponse(
        [
            _repo("Example-Owner/blog", "trunk", private=True),
            _repo("someone-else/blog"),
            # GitHub's canonical case need not match how the owner was configured
            _repo("example-owner/api"),
        ]
    )

    repos = await github.GitHub("gho_abc").list_repos()

    assert [r["full_name"] for r in repos] == ["Example-Owner/blog", "example-owner/api"]
    assert repos[0] == {
        "full_name": "Example-Owner/blog",
        "default_branch": "trunk",
        "private": True,
    }


async def test_list_repos_without_an_owner_configured_filters_nothing(http, monkeypatch):
    # With no owner set nothing can deploy anyway; hiding every repo would only
    # look broken, so the OIDC check is left to speak for itself.
    monkeypatch.setattr(config, "OIDC_OWNER", "")
    http.routes["/user/repos"] = FakeResponse([_repo("anyone/thing")])

    assert len(await github.GitHub("gho_abc").list_repos()) == 1


async def test_list_branches_returns_names(http):
    http.routes["/repos/o/r/branches"] = FakeResponse(
        [{"name": "main"}, {"name": "trunk"}, {"no": "name"}]
    )

    assert await github.GitHub("gho_abc").list_branches("o/r") == ["main", "trunk"]
    _, _, kwargs = http.requests[0]
    assert kwargs["params"] == {"per_page": config.GITHUB_PAGE_SIZE}


@pytest.mark.parametrize("repo", ["../../user", "owner", "owner/name/extra", "o r/x"])
async def test_a_repo_name_cannot_steer_the_request_elsewhere(http, repo):
    # The repo reaches the URL path, so it is checked before the call is made.
    with pytest.raises(github.GitHubApiError, match="not a valid owner/repo"):
        await github.GitHub("gho_abc").list_branches(repo)
    with pytest.raises(github.GitHubApiError, match="not a valid owner/repo"):
        await github.GitHub("gho_abc").read_file(repo, "console.toml", "main")
    assert http.requests == []  # nothing was ever sent


async def test_a_revoked_token_says_to_reconnect(http):
    http.routes["/user"] = FakeResponse({"message": "Bad credentials"}, status_code=401)

    with pytest.raises(github.GitHubApiError, match="Reconnect"):
        await github.GitHub("gho_abc").login()


# --- reading a file --------------------------------------------------------


def contents(text, **overrides):
    """The contents API shape: base64 wrapped in JSON, and GitHub wraps the
    payload at 60 columns. Decoding that naively has bitten this project
    before, so the fixture keeps the newlines."""
    encoded = base64.encodebytes(text.encode()).decode()
    assert "\n" in encoded.strip() or len(encoded) < 62
    return FakeResponse(
        {
            "type": "file",
            "encoding": "base64",
            "size": len(text),
            "content": encoded,
            **overrides,
        }
    )


async def test_read_file_decodes_newline_wrapped_base64(http):
    body = 'secrets = ["DATABASE_URL"]\n\n[app]\nname = "demo"\nport = 8080\n' * 3
    http.routes["/contents/console.toml"] = contents(body)

    assert await github.GitHub("gho_abc").read_file("o/r", "console.toml", "main") == body
    _, url, kwargs = http.requests[0]
    assert url.endswith("/repos/o/r/contents/console.toml")
    assert kwargs["params"] == {"ref": "main"}


async def test_read_file_missing_is_not_an_api_error(http):
    http.routes["/contents/console.toml"] = FakeResponse({"message": "Not Found"}, 404)

    with pytest.raises(github.FileNotFound):
        await github.GitHub("gho_abc").read_file("o/r", "console.toml", "main")


async def test_read_file_rejects_an_empty_content_field(http):
    # How GitHub answers for a file over ~1MB: decoding it would look like an
    # empty console.toml rather than a file we refused to read.
    http.routes["/contents/console.toml"] = contents("x", content="", size=2_000_000)

    with pytest.raises(github.GitHubApiError, match="not a readable file"):
        await github.GitHub("gho_abc").read_file("o/r", "console.toml", "main")


async def test_read_file_rejects_an_oversized_file(http, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_FILE_MAX_BYTES", 8)
    http.routes["/contents/console.toml"] = contents("way past the cap")

    with pytest.raises(github.GitHubApiError, match="larger than"):
        await github.GitHub("gho_abc").read_file("o/r", "console.toml", "main")


async def test_read_file_rejects_a_directory(http):
    http.routes["/contents/console.toml"] = contents("x", type="dir")

    with pytest.raises(github.GitHubApiError, match="not a readable file"):
        await github.GitHub("gho_abc").read_file("o/r", "console.toml", "main")
