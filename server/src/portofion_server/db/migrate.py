from __future__ import annotations

from importlib.resources import files

from alembic import command
from alembic.config import Config

from portofion_server.settings import get_settings


def alembic_config(url: str | None = None, connection=None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(files("portofion_server") / "migrations"))
    cfg.set_main_option("sqlalchemy.url", (url or get_settings().database_url).replace("%", "%%"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def upgrade(url: str | None = None, revision: str = "head", connection=None) -> None:
    command.upgrade(alembic_config(url, connection), revision)


def downgrade(url: str | None = None, revision: str = "base", connection=None) -> None:
    command.downgrade(alembic_config(url, connection), revision)
