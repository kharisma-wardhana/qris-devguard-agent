"""Shared test setup (p6).

- Windows: psycopg's async driver needs the selector event loop (app.db.connect.use_selector_loop).
- `db_url` (session): a reachable PostgreSQL with a throwaway schema `p6test_<random>`, dropped afterwards.
  Without a reachable database every test that uses it is SKIPPED with a reason, never passed. CI has no
  PostgreSQL (p6 intake decision), so `db` tests skip there and run locally against the Podman Postgres.
"""

from __future__ import annotations

import secrets

import pytest

from app.db.connect import DatabaseNotConfigured, conninfo, dsn, use_selector_loop

use_selector_loop()


@pytest.fixture(scope="session")
def db_url():
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
