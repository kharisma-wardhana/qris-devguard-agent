"""Least-privilege runtime roles (p7 spec Behaviour §4, AC #11, #14; TRD §13.3).

`GRANTS` is the role table from the spec, as SQL privileges per table. `apply()` runs as the owner
(`scripts/db_migrate.py`) after our migrations and the LangGraph checkpoint tables exist:

- creates each role if absent (LOGIN, no superuser/createdb/createrole/replication, NOINHERIT);
- sets its password from the environment only when the current one doesn't work, so a re-run with the same
  `.env` changes nothing; passwords travel as `psycopg.sql.Literal`, never in a log or exception text;
- revokes everything the role has on the schema's tables, then grants exactly `GRANTS` (idempotent, and a
  stale extra right from an older run is removed).

Role names are parameters: tests pass random throwaway names so they never touch the real roles' passwords.
A subprocess under test gets them through `DEVGUARD_DB_ROLE_SUFFIX` (appended to every configured name).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

import psycopg
from psycopg import sql

from app.agent import config

CHECKPOINT_DATA = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

GRANTS: dict[str, list[tuple[str, str]]] = {
    # API + agent: threads, approvals (create and decide), LangGraph checkpoints. No tickets, no audit, no DDL.
    "app": [
        ("SELECT, INSERT, UPDATE", "agent_threads"),
        ("SELECT, INSERT", "approval_requests"),
        ("UPDATE (status, approver_id, decision_reason, decided_at)", "approval_requests"),
        *[("SELECT, INSERT, UPDATE, DELETE", t) for t in CHECKPOINT_DATA],
        ("SELECT", "checkpoint_migrations"),
    ],
    # MCP create_support_ticket: read the approval, mark it executed/expired, insert the ticket.
    "ticket": [
        ("SELECT", "approval_requests"),
        ("UPDATE (status, executed_at, ticket_id)", "approval_requests"),
        ("SELECT, INSERT", "sandbox_tickets"),
    ],
    # MCP audit writer: append only.
    "audit": [
        ("INSERT", "tool_audit_logs"),
    ],
}


class RolePasswordMissing(RuntimeError):
    """A role's password key is unset. The message names the key, never a value."""

    def __init__(self, key: str) -> None:
        super().__init__(f"{key} is not set (environment or .env); run `uv run python scripts/init_env.py "
                         "--add-missing`, then `uv run python scripts/db_migrate.py`")
        self.key = key


SUFFIX_ENV = "DEVGUARD_DB_ROLE_SUFFIX"
SUFFIX = re.compile(r"^[a-z0-9_]{0,24}$")


def role_config() -> dict[str, dict[str, str]]:
    """Configured roles, with the test-only name suffix applied (empty in normal use)."""
    suffix = os.environ.get(SUFFIX_ENV, "")
    if not SUFFIX.match(suffix):
        raise ValueError(f"{SUFFIX_ENV} must match [a-z0-9_]{{0,24}}")
    return {k: {**v, "name": v["name"] + suffix} for k, v in config.agent()["database"]["roles"].items()}


def _password_works(conninfo: str, role: str, password: str) -> bool:
    try:
        with psycopg.connect(conninfo, user=role, password=password, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


def apply(conn: psycopg.Connection, names: Mapping[str, str], passwords: Mapping[str, str],
          conninfo: str | None = None) -> list[tuple[str, str]]:
    """Create/align roles `names` (key -> role name). Returns (role, 'created'|'password set'|'unchanged').

    `conn` is an autocommit owner connection whose search_path is the target schema. `conninfo`, when given,
    lets an existing role's password be checked by logging in; without it the password is always (re)set.
    """
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    out = []
    for key in GRANTS:
        role, password = names[key], passwords[key]
        ident = sql.Identifier(role)
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                 "NOREPLICATION PASSWORD {}").format(ident, sql.Literal(password)))
            status = "created"
        elif conninfo and _password_works(conninfo, role, password):
            status = "unchanged"
        else:
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(ident, sql.Literal(password)))
            status = "password set"
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), ident))
        conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(sql.Identifier(schema), ident))
        for privileges, table in GRANTS[key]:
            conn.execute(sql.SQL("GRANT " + privileges + " ON {} TO {}").format(sql.Identifier(table), ident))
        out.append((role, status))
    return out


def drop(conn: psycopg.Connection, roles: list[str]) -> None:
    """Tests only: remove throwaway roles and everything granted to them."""
    for role in roles:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
