"""Least-privilege runtime roles (p7 spec Behaviour §4, AC #11, #14).

One assertion per "may not" cell of the spec's role table, each an attempted statement that must fail with a
permission error; the "may" cells are exercised with real statements too. Throwaway roles only (db_roles).
"""

from __future__ import annotations

import uuid

import pytest

TID = "rolesthread" + "x" * 10


@pytest.fixture(scope="module")
def seeded(migrated_db_url):
    """One thread and one approved approval, inserted by the owner."""
    import psycopg

    approval = str(uuid.uuid4())
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        c.execute("INSERT INTO agent_threads (thread_id, owner_id) VALUES (%s, 'dev-1') ON CONFLICT DO NOTHING",
                  (TID,))
        c.execute("INSERT INTO approval_requests (approval_id, thread_id, run_id, requester_id, requester_role, "
                  "action_type, action_payload, action_hash, status, expires_at) VALUES (%s, %s, %s, 'dev-1', "
                  "'developer', 'create_support_ticket', '{}', %s, 'approved', now() + interval '1 hour')",
                  (approval, TID, str(uuid.uuid4()), "a" * 64))
    return approval


def run(url: str, stmt: str, params: tuple = ()) -> None:
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(stmt, params)


def denied(url: str, stmt: str, params: tuple = ()) -> None:
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run(url, stmt, params)


AUDIT_ROW = ("INSERT INTO tool_audit_logs (correlation_id, actor_sub, actor_role, tool, phase, outcome) "
             "VALUES (%s, 'dev-1', 'developer', 'lookup_response_code', 'result', 'OK')")


def ticket_insert(approval: str) -> tuple[str, tuple]:
    return ("INSERT INTO sandbox_tickets (ticket_id, idempotency_key, approval_id, thread_id, requester_id, title, "
            "body_sanitized, evidence_ids, priority) VALUES (%s, %s, %s, %s, 'dev-1', 't', 'b', '[]', 'low')",
            (str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex, approval, TID))


# --- devguard_app ------------------------------------------------------------------------------------

@pytest.mark.db
def test_app_may(db_roles, seeded):
    url = db_roles.url("app")
    tid = "apptest" + uuid.uuid4().hex[:12]
    run(url, "INSERT INTO agent_threads (thread_id, owner_id) VALUES (%s, 'dev-2')", (tid,))
    run(url, "UPDATE agent_threads SET status = 'running' WHERE thread_id = %s", (tid,))
    run(url, "SELECT * FROM approval_requests WHERE approval_id = %s FOR UPDATE", (seeded,))
    run(url, "UPDATE approval_requests SET decision_reason = 'ok' WHERE approval_id = %s", (seeded,))
    run(url, "SELECT count(*) FROM checkpoints")
    run(url, "DELETE FROM checkpoint_writes WHERE thread_id = 'none'")
    run(url, "SELECT count(*) FROM checkpoint_migrations")


@pytest.mark.db
@pytest.mark.parametrize("stmt", [
    "SELECT count(*) FROM sandbox_tickets",
    "SELECT count(*) FROM tool_audit_logs",
    "UPDATE approval_requests SET executed_at = now() WHERE false",
    "UPDATE approval_requests SET ticket_id = NULL WHERE false",
    "DELETE FROM approval_requests WHERE false",
    "DELETE FROM agent_threads WHERE false",
    "TRUNCATE agent_threads",
    "CREATE TABLE app_ddl (x int)",
    "INSERT INTO checkpoint_migrations (v) VALUES (999)",
])
def test_app_may_not(db_roles, stmt):
    denied(db_roles.url("app"), stmt)


@pytest.mark.db
def test_app_may_not_insert_ticket_or_audit(db_roles, seeded):
    denied(db_roles.url("app"), *ticket_insert(seeded))
    denied(db_roles.url("app"), AUDIT_ROW, (uuid.uuid4().hex,))


# --- devguard_ticket ---------------------------------------------------------------------------------

@pytest.mark.db
def test_ticket_may(db_roles, seeded):
    url = db_roles.url("ticket")
    run(url, "SELECT * FROM approval_requests WHERE approval_id = %s FOR UPDATE", (seeded,))
    run(url, *ticket_insert(seeded))                       # FK checks to threads/approvals work without SELECT
    run(url, "UPDATE approval_requests SET status = 'executed', executed_at = now() WHERE approval_id = %s",
        (seeded,))
    run(url, "SELECT count(*) FROM sandbox_tickets")


@pytest.mark.db
@pytest.mark.parametrize("stmt", [
    "SELECT count(*) FROM agent_threads",
    "UPDATE agent_threads SET status = 'idle' WHERE false",
    "SELECT count(*) FROM checkpoints",
    "SELECT count(*) FROM tool_audit_logs",
    "UPDATE approval_requests SET approver_id = 'x' WHERE false",
    "UPDATE approval_requests SET action_payload = '{}' WHERE false",
    "DELETE FROM sandbox_tickets WHERE false",
    "UPDATE sandbox_tickets SET title = 'x' WHERE false",
    "DELETE FROM approval_requests WHERE false",
    "TRUNCATE sandbox_tickets",
    "CREATE TABLE ticket_ddl (x int)",
])
def test_ticket_may_not(db_roles, stmt):
    denied(db_roles.url("ticket"), stmt)


@pytest.mark.db
def test_ticket_may_not_create_approval_or_audit(db_roles):
    denied(db_roles.url("ticket"), "INSERT INTO approval_requests (approval_id, thread_id, run_id, requester_id, "
           "requester_role, action_type, action_payload, action_hash, status, expires_at) VALUES "
           "(gen_random_uuid(), %s, gen_random_uuid(), 'x', 'developer', 'create_support_ticket', '{}', %s, "
           "'approved', now())", (TID, "b" * 64))
    denied(db_roles.url("ticket"), AUDIT_ROW, (uuid.uuid4().hex,))


# --- devguard_audit ----------------------------------------------------------------------------------

@pytest.mark.db
def test_audit_may_insert(db_roles):
    run(db_roles.url("audit"), AUDIT_ROW, (uuid.uuid4().hex,))


@pytest.mark.db
@pytest.mark.parametrize("stmt", [
    "SELECT count(*) FROM tool_audit_logs",
    "UPDATE tool_audit_logs SET outcome = 'OK' WHERE false",
    "DELETE FROM tool_audit_logs WHERE false",
    "TRUNCATE tool_audit_logs",
    "SELECT count(*) FROM approval_requests",
    "SELECT count(*) FROM agent_threads",
    "SELECT count(*) FROM sandbox_tickets",
    "SELECT count(*) FROM checkpoints",
    "CREATE TABLE audit_ddl (x int)",
])
def test_audit_may_not(db_roles, stmt):
    denied(db_roles.url("audit"), stmt)


# --- p8: long-term memory (spec AC #27) ---------------------------------------------------------------

PREF_ROW = ("INSERT INTO long_term_memory (memory_id, owner_id, scope, memory_type, pref_key, content_sanitized, "
            "status, confidence, proposed_by, expires_at) VALUES (%s, 'dev-roles', 'user', 'user_preference', "
            "'language', 'id', 'active', 'explicit', 'dev-roles', now() + interval '1 day')")


@pytest.mark.db
def test_app_may_read_write_memory_and_history(db_roles):
    url = db_roles.url("app")
    mid = str(uuid.uuid4())
    run(url, PREF_ROW, (mid,))
    run(url, "UPDATE long_term_memory SET content_sanitized = 'en', version = 2 WHERE memory_id = %s", (mid,))
    run(url, "INSERT INTO long_term_memory_history (memory_id, version, content_sanitized, changed_by) "
             "VALUES (%s, 1, 'id', 'dev-roles')", (mid,))
    run(url, "UPDATE long_term_memory_history SET content_sanitized = '' WHERE memory_id = %s", (mid,))
    run(url, "SELECT count(*) FROM long_term_memory_history")


@pytest.mark.db
@pytest.mark.parametrize("stmt", [
    "DELETE FROM long_term_memory WHERE false",
    "DELETE FROM long_term_memory_history WHERE false",
    "TRUNCATE long_term_memory",
    "UPDATE long_term_memory_history SET changed_by = 'x' WHERE false",
])
def test_app_may_not_hard_delete_memory(db_roles, stmt):
    denied(db_roles.url("app"), stmt)


@pytest.mark.db
@pytest.mark.parametrize("role", ["ticket", "audit"])
@pytest.mark.parametrize("stmt", [
    "SELECT count(*) FROM long_term_memory",
    "SELECT count(*) FROM long_term_memory_history",
    "UPDATE long_term_memory SET title = '' WHERE false",
])
def test_ticket_and_audit_may_not_touch_memory(db_roles, role, stmt):
    denied(db_roles.url(role), stmt)


@pytest.mark.db
def test_ticket_and_audit_may_not_insert_memory(db_roles):
    for role in ("ticket", "audit"):
        denied(db_roles.url(role), PREF_ROW, (str(uuid.uuid4()),))


# --- creation is idempotent and never leaks a password ----------------------------------------------

@pytest.mark.db
def test_apply_is_idempotent_and_detects_unchanged(db_roles, migrated_db_url):
    import psycopg

    from app.db import roles
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        again = roles.apply(c, db_roles.names, db_roles.passwords, migrated_db_url)
    assert [s for _, s in again] == ["unchanged"] * 3
    test_audit_may_not(db_roles, "SELECT count(*) FROM tool_audit_logs")      # grants still exact


def test_role_conninfo_names_missing_key(monkeypatch, tmp_path):
    from app.db import connect
    from app.db.roles import RolePasswordMissing
    monkeypatch.setattr(connect, "ROOT", tmp_path)
    monkeypatch.delenv("DEVGUARD_TICKET_DB_PASSWORD", raising=False)
    with pytest.raises(RolePasswordMissing) as exc:
        connect.role_conninfo("ticket", url="postgresql://devguard:SYNowner1@localhost:5432/devguard")
    assert "DEVGUARD_TICKET_DB_PASSWORD" in str(exc.value) and "SYNowner1" not in str(exc.value)


def test_role_conninfo_swaps_user_never_owner(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict

    from app.db import connect
    monkeypatch.setenv("DEVGUARD_APP_DB_PASSWORD", "SYNapp42")
    monkeypatch.delenv("DEVGUARD_DB_ROLE_SUFFIX", raising=False)
    info = conninfo_to_dict(connect.role_conninfo("app", url="postgresql://devguard:SYNowner1@localhost:5432/db"))
    assert info["user"] == "devguard_app" and info["password"] == "SYNapp42" and info["dbname"] == "db"


def test_suffix_is_validated(monkeypatch):
    from app.db import roles
    monkeypatch.setenv(roles.SUFFIX_ENV, "x; DROP ROLE devguard")
    with pytest.raises(ValueError):
        roles.role_config()
    monkeypatch.setenv(roles.SUFFIX_ENV, "_tab12")
    assert roles.role_config()["audit"]["name"] == "devguard_audit_tab12"
