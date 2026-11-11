"""Shared test setup (p6).

- Windows: psycopg's async driver needs the selector event loop (app.db.connect.use_selector_loop).
- Database fixtures. Each one gives a throwaway schema `p6test_<random>` (dropped afterwards) on a reachable
  PostgreSQL; without one, every test that uses them is SKIPPED with a reason, never passed. CI has no
  PostgreSQL (p6 intake decision), so `db` tests skip there and run locally against the Podman Postgres.
    db_url           session schema, empty
    fresh_db_url     a new empty schema per test (migration tests)
    migrated_db_url  session schema with our migrations and the LangGraph checkpoint tables
    db_roles         (p7) throwaway least-privilege roles on migrated_db_url: names devguard_<key>_t<hex>,
                     random passwords, dropped afterwards. Never the real roles (their passwords are in .env).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets

import pytest

from app.db.connect import DatabaseNotConfigured, conninfo, dsn, use_selector_loop

use_selector_loop()


@contextlib.contextmanager
def _schema():
    import psycopg

    try:
        base = dsn(test=True)
        admin = psycopg.connect(conninfo(base), autocommit=True)
    except (DatabaseNotConfigured, psycopg.Error) as exc:
        pytest.skip(f"no reachable PostgreSQL for db tests ({type(exc).__name__}); start it with "
                    "`podman compose up -d postgres`")
    schema = f"p6test_{secrets.token_hex(4)}"
    with admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            yield conninfo(base, schema=schema)
        finally:
            admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture(scope="session")
def db_url():
    with _schema() as url:
        yield url


@pytest.fixture
def fresh_db_url():
    with _schema() as url:
        yield url


@pytest.fixture(scope="session")
def migrated_db_url():
    import psycopg

    from app.db import migrate

    with _schema() as url:
        with psycopg.connect(url, autocommit=True) as conn:
            migrate.apply(conn)
        asyncio.run(migrate.setup_checkpointer(url))
        yield url


class DbRoles:
    """Throwaway runtime roles for one test session (p7). `url(key)` is that role's conninfo on the schema."""

    def __init__(self, owner_url: str, suffix: str, names: dict[str, str], passwords: dict[str, str]):
        self.owner_url, self.suffix, self.names, self.passwords = owner_url, suffix, names, passwords

    def url(self, key: str) -> str:
        from app.db.connect import role_conninfo
        return role_conninfo(key, url=self.owner_url, name=self.names[key], password=self.passwords[key])

    def env(self) -> dict[str, str]:
        """Environment for a subprocess that must connect as these roles."""
        from app.db.roles import SUFFIX_ENV, role_config
        cfg = role_config()
        return {SUFFIX_ENV: self.suffix, **{cfg[k]["password_env"]: v for k, v in self.passwords.items()}}


@pytest.fixture(scope="session")
def db_roles(migrated_db_url):
    import psycopg

    from app.db import roles

    suffix = f"_t{secrets.token_hex(4)}"
    names = {k: v["name"] + suffix for k, v in roles.role_config().items()}
    passwords = {k: secrets.token_hex(16) for k in names}
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        roles.apply(conn, names, passwords)
    try:
        yield DbRoles(migrated_db_url, suffix, names, passwords)
    finally:
        with psycopg.connect(migrated_db_url, autocommit=True) as conn:
            roles.drop(conn, list(names.values()))


# --- p7: evidence capture for scripts/eval_security.py --------------------------------------------------

def pytest_runtest_logreport(report):
    """With DEVGUARD_SECURITY_EVIDENCE=<file>, append one JSON line per test outcome (node ID, outcome, duration
    and any record_property values). Setup failures and skips are recorded too, so a skipped test is never
    mistaken for a pass. Off by default; nothing is written in normal runs."""
    import json
    import os

    path = os.environ.get("DEVGUARD_SECURITY_EVIDENCE")
    if not path or not (report.when == "call" or report.outcome != "passed"):
        return
    rec = {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
           "duration": round(report.duration, 3), "properties": {k: str(v) for k, v in report.user_properties}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
