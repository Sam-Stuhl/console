"""Running migrations must not silence the app.

Migrations run inside the lifespan, and alembic's env.py configures logging
from alembic.ini. logging.config.fileConfig disables every pre-existing logger
unless told otherwise, which switched off uvicorn's access and error loggers
the moment the console started. The container served traffic normally and
logged nothing after its first two lines, so the box could not be diagnosed
from its own logs at all.

Nothing about that is visible in the app's behaviour, which is why it survived
this long and why it is pinned here.
"""

import logging
from logging.config import fileConfig
from pathlib import Path

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def test_alembic_ini_config_leaves_existing_loggers_alone():
    existing = logging.getLogger("uvicorn.access")
    existing.disabled = False

    fileConfig(str(ALEMBIC_INI), disable_existing_loggers=False)

    assert not existing.disabled, "configuring alembic's logging silenced uvicorn"


def test_the_default_would_have_silenced_it():
    # The failure mode this guards against, so the assertion above is not
    # mistaken for a tautology: the default really does disable it.
    existing = logging.getLogger("uvicorn.access")
    existing.disabled = False

    fileConfig(str(ALEMBIC_INI))  # default disable_existing_loggers=True

    assert existing.disabled
    existing.disabled = False  # leave the logger as we found it


def test_env_py_passes_the_flag():
    """The behaviour above only helps if env.py actually asks for it."""
    env_py = (Path(__file__).resolve().parents[1] / "alembic" / "env.py").read_text()

    assert "disable_existing_loggers=False" in env_py
