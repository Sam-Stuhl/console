import base64

import httpx
import pytest

from conftest import GITHUB_CLIENT_ID, FakeResponse
from console import config, github, settings_store


@pytest.fixture(autouse=True)
def http(github_http):
    """Every test here talks to the stubbed GitHub."""
    return github_http


# --- authorization code flow -----------------------------------------------


def test_authorize_url_carries_what_github_needs():
    url = github.authorize_url("Iv1.abc", "https://console.example.com/api/github/callback", "st8")

    assert url.startswith(config.GITHUB_AUTHORIZE_URL + "?")
    assert "client_id=Iv1.abc" in url
    assert "state=st8" in url
    assert "scope=repo" in url
    assert "redirect_uri=https%3A%2F%2Fconsole.example.com%2Fapi%2Fgithub%2Fcallback" in url


async def test_exchange_code_returns_the_token(http):
    http.routes["login/oauth/access_token"] = FakeResponse({"access_token": "gho_abc"})

    token = await github.exchange_code("Iv1.abc", "sec", "code-1", "https://c/cb")

    assert token == "gho_abc"
    _, _, kwargs = http.requests[0]
    assert kwargs["data"]["client_secret"] == "sec"
    assert kwargs["data"]["code"] == "code-1"


async def test_exchange_code_surfaces_githubs_reason(http):
    http.routes["login/oauth/access_token"] = FakeResponse(
        {"error": "bad_verification_code", "error_description": "code expired"}
    )

    with pytest.raises(github.GitHubApiError, match="code expired"):
        await github.exchange_code("Iv1.abc", "sec", "stale", "https://c/cb")


async def test_unreachable_github_is_a_readable_error(http):
    http.routes["login/oauth/access_token"] = httpx.ConnectError("dns")

    with pytest.raises(github.GitHubApiError, match="could not reach GitHub"):
        await github.exchange_code("Iv1.abc", "sec", "code-1", "https://c/cb")


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


async def test_client_id_prefers_the_saved_one_over_the_env(db, monkeypatch):
    # Same arrangement as the Cloudflare credentials: Settings wins, so this can
    # be set up in the browser without editing a file on the box.
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "Iv1.fromenv")
    async with db() as session:
        assert await github.client_id(session) == "Iv1.fromenv"
        await settings_store.set_value(
            session, settings_store.GITHUB_CLIENT_ID, "Iv1.fromsettings"
        )
        await session.commit()
        assert await github.client_id(session) == "Iv1.fromsettings"


@pytest.mark.parametrize("missing", ["id", "secret"])
async def test_half_a_configured_app_is_not_set_up(db, monkeypatch, missing):
    # Either half missing means the redirect cannot complete, so both report the
    # same thing rather than failing later, mid-exchange.
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")
    async with db() as session:
        if missing != "id":
            await settings_store.set_value(
                session, settings_store.GITHUB_CLIENT_ID, "Iv1.abc"
            )
        if missing != "secret":
            await settings_store.set_value(
                session, settings_store.GITHUB_CLIENT_SECRET, "sec"
            )
        await session.commit()
        with pytest.raises(github.NotSetUp):
            await github.app_credentials(session)


async def test_a_fully_configured_app_resolves(db, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_CLIENT_ID, "Iv1.abc")
        await settings_store.set_value(session, settings_store.GITHUB_CLIENT_SECRET, "sec")
        await session.commit()
        assert await github.app_credentials(session) == ("Iv1.abc", "sec")


# --- repo listing ----------------------------------------------------------


def _repo(full_name, default_branch="main", private=False):
    return {
        "full_name": full_name,
        "default_branch": default_branch,
        "private": private,
        "pushed_at": "2026-08-01T12:00:00Z",
    }


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
        "pushed_at": "2026-08-01T12:00:00Z",
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


async def test_resolve_commit_returns_sha_and_message(github_http):
    github_http.routes["/commits/main"] = FakeResponse(
        {"sha": "d" * 40, "commit": {"message": "first line\n\nbody"}}
    )
    sha, message = await github.GitHub("tok").resolve_commit("example-owner/demo", "main")
    assert sha == "d" * 40
    assert message == "first line\n\nbody"
    assert github_http.requests[0][1].endswith("/repos/example-owner/demo/commits/main")


async def test_resolve_commit_missing_ref_is_not_found(github_http):
    github_http.routes["/commits/gone"] = FakeResponse({"message": "Not Found"}, 404)
    with pytest.raises(github.FileNotFound):
        await github.GitHub("tok").resolve_commit("example-owner/demo", "gone")


async def test_resolve_commit_without_a_sha_is_an_api_error(github_http):
    github_http.routes["/commits/odd"] = FakeResponse({"commit": {}})
    with pytest.raises(github.GitHubApiError):
        await github.GitHub("tok").resolve_commit("example-owner/demo", "odd")
