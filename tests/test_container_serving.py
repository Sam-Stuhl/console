"""How the container runs uvicorn, where getting it wrong only shows in
production.

Behind Traefik the app has to be told to trust X-Forwarded-Proto, or it
believes every request arrived over plain http. In development, where there is
no proxy, everything looks right; on the server the OAuth redirect_uri comes
out as http:// and GitHub refuses it, and cookies marked secure-if-https never
are. Nothing catches that except the server itself, so the flag is pinned here.
"""

from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()


def test_uvicorn_trusts_the_proxys_forwarded_headers():
    assert "--forwarded-allow-ips" in DOCKERFILE, (
        "without this uvicorn honors X-Forwarded-Proto only from 127.0.0.1, "
        "and Traefik reaches the container from a docker network address"
    )


def test_python_output_is_unbuffered():
    # Not what silenced the logs (that was alembic's fileConfig), but still the
    # right setting for a container: no reason to hold output in a buffer.
    assert "PYTHONUNBUFFERED=1" in DOCKERFILE
