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
