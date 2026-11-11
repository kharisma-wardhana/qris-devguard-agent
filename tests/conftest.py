"""Shared test setup (p6).

- Windows: psycopg's async driver needs the selector event loop (app.db.connect.use_selector_loop).
- Database fixtures. Each one gives a throwaway schema `p6test_<random>` (dropped afterwards) on a reachable
  PostgreSQL; without one, every test that uses them is SKIPPED with a reason, never passed. CI has no
  PostgreSQL (p6 intake decision), so `db` tests skip there and run locally against the Podman Postgres.
    db_url           session schema, empty
    fresh_db_url     a new empty schema per test (migration tests)
    migrated_db_url  session schema with our migrations and the LangGraph checkpoint tables
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
