import pytest

from console import main


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    monkeypatch.setattr(main, "DIST", tmp_path)
    return tmp_path


async def test_spa_route_serves_index(client, dist):
    response = await client.get("/projects/abc")
    assert response.status_code == 200
    assert "id=root" in response.text


@pytest.mark.parametrize("path", ["/api/nothing", "/api/projects/x/nothing", "/v1/nothing"])
async def test_unknown_api_path_is_a_json_404_not_the_spa(client, dist, path):
    # A script with a typo in an API path must get a 404 it can act on, not a
    # 200 with HTML it then fails to parse.
    response = await client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_fallback_never_serves_a_file_outside_the_build(
    client, dist, tmp_path_factory
):
    # "/%2F<abs path>" reaches the route as an absolute path (as "//<abs path>"
    # does over the wire; httpx would read that as a host instead).
    secret = tmp_path_factory.mktemp("outside") / "key"
    secret.write_text("not for the browser")
    response = await client.get("/%2F" + str(secret).lstrip("/"))
    assert response.status_code == 200
    assert "id=root" in response.text


async def test_a_double_slash_api_path_is_still_a_404(client, dist):
    assert (await client.get("/%2Fapi/nothing")).status_code == 404


async def test_files_inside_the_build_are_still_served(client, dist):
    (dist / "console.svg").write_text("<svg/>")
    response = await client.get("/console.svg")
    assert response.status_code == 200
    assert response.text == "<svg/>"


async def test_a_null_byte_in_the_path_gets_the_spa_not_a_500(client, dist):
    response = await client.get("/a%00b")
    assert response.status_code == 200
    assert "id=root" in response.text
