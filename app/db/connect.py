"""PostgreSQL connection settings (p6 spec Architecture).

- The DSN comes from `database.url_env` (DATABASE_URL) in the environment, else from the repo's `.env`.
  The SQLAlchemy-style `postgresql+psycopg://` scheme is normalised for psycopg.
- `conninfo()` adds the connect timeout and, for tests, a `search_path` so each test session works in its
  own throwaway schema.
- Nothing here prints or logs a DSN; error messages never include it.
- psycopg's async driver cannot run on Windows' default Proactor loop: `use_selector_loop()` switches the
  policy on Windows (API server, migrations, tests). It is a no-op elsewhere.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from psycopg.conninfo import make_conninfo

from app.agent import config

ROOT = Path(__file__).resolve().parents[2]


class DatabaseNotConfigured(RuntimeError):
    """No DSN in the environment or .env. The message never holds a DSN."""


DOTENV_OVERRIDE = "DEVGUARD_DB_DOTENV"      # p7: path of the .env to read database settings from (tests: none)


def _from_env_file(key: str) -> str | None:
    override = os.environ.get(DOTENV_OVERRIDE)
    env = Path(override) if override else ROOT / ".env"
    if not env.is_file():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == key and v.strip() and "replace-me" not in v:
            return v.strip()
    return None


def dsn(*, test: bool = False) -> str:
    db = config.agent()["database"]
    raw = None
    if test:
        raw = os.environ.get(db["test_url_env"])
    raw = raw or os.environ.get(db["url_env"]) or _from_env_file(db["url_env"])
    if not raw:
        raise DatabaseNotConfigured(f"{db['url_env']} is not set (environment or .env)")
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


def conninfo(url: str | None = None, *, schema: str | None = None) -> str:
    kw: dict[str, object] = {"connect_timeout": config.agent()["database"]["connect_timeout_seconds"]}
    if schema:
        kw["options"] = f"-c search_path={schema}"
    return make_conninfo(url or dsn(), **kw)


def role_password(key: str) -> str:
    """A runtime role's password (p7) from the environment, else `.env`. Raises RolePasswordMissing (names the key)."""
    from app.db.roles import RolePasswordMissing

    value = os.environ.get(key) or _from_env_file(key)
    if not value or "replace-me" in value:
        raise RolePasswordMissing(key)
    return value


def role_conninfo(role: str, *, url: str | None = None, schema: str | None = None,
                  name: str | None = None, password: str | None = None) -> str:
    """Conninfo for runtime role `role` ('app' | 'ticket' | 'audit'): DATABASE_URL's host, port and database with
    the role's user and password swapped in (p7 spec Architecture). Never falls back to the owner role.
    `name`/`password` override the configured ones (tests use throwaway roles)."""
    from psycopg.conninfo import conninfo_to_dict

    from app.db.roles import role_config

    cfg = role_config()[role]
    parts = conninfo_to_dict(conninfo(url or dsn(), schema=schema))
    parts["user"] = name or cfg["name"]
    parts["password"] = password if password is not None else role_password(cfg["password_env"])
    return make_conninfo(**parts)


def use_selector_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
