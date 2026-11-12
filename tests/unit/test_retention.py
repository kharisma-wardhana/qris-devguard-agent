"""Retention purge on PostgreSQL (p8 spec Behaviour §6; AC #4, #5, #26, #27).

Each test gets its own freshly migrated schema (with the LangGraph checkpoint tables), because a purge acts on
every row of its schema. Rows are seeded just inside and just outside each period.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid

import psycopg
import pytest

from app.db import retention
from app.db import store as db_store
from app.memory import store as memory

pytestmark = pytest.mark.db
P = {"checkpoint_days": 30, "approval_days": 90}


@pytest.fixture
def url(fresh_db_url):
    from app.db import migrate
    with psycopg.connect(fresh_db_url, autocommit=True) as conn:
        migrate.apply(conn)
    asyncio.run(migrate.setup_checkpointer(fresh_db_url))
    return fresh_db_url


def tid() -> str:
    return "t" + secrets.token_hex(10)


def thread(c, *, days: float, status: str = "completed", checkpoints: bool = True) -> str:
    t = tid()
    c.execute("INSERT INTO agent_threads (thread_id, owner_id, status, updated_at, created_at) "
              "VALUES (%s, 'dev-1', %s, now() - make_interval(days => %s), now() - make_interval(days => %s))",
              (t, status, days, days))
    if checkpoints:
        c.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                  "VALUES (%s, '', 'c1', '{}', '{}')", (t,))
        c.execute("INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
                  "VALUES (%s, '', 'messages', '1', 'json', '\\x00')", (t,))
        c.execute("INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, "
                  "type, blob, task_path) VALUES (%s, '', 'c1', 'task', 0, 'messages', 'json', '\\x00', '')", (t,))
    return t


def approval(c, t: str, *, days: float, status: str = "executed") -> str:
    aid = str(uuid.uuid4())
    c.execute("INSERT INTO approval_requests (approval_id, thread_id, run_id, requester_id, requester_role, "
              "action_type, action_payload, action_hash, status, expires_at, created_at) VALUES (%s, %s, %s, "
              "'dev-1', 'developer', 'create_support_ticket', %s, %s, %s, now(), now() - make_interval(days => %s))",
              (aid, t, str(uuid.uuid4()), json.dumps({"title": "x"}), secrets.token_hex(32), status, days))
    return aid


def ticket(c, t: str, aid: str, *, days: float) -> str:
    tk = str(uuid.uuid4())
    c.execute("INSERT INTO sandbox_tickets (ticket_id, idempotency_key, approval_id, thread_id, requester_id, title, "
              "body_sanitized, evidence_ids, priority, created_at) VALUES (%s, %s, %s, %s, 'dev-1', 'Escalation: x', "
              "'body', '[]', 'low', now() - make_interval(days => %s))", (tk, secrets.token_hex(32), aid, t, days))
    return tk


def exists(c, table: str, column: str, value: str) -> bool:
    return c.execute(f"SELECT count(*) AS n FROM {table} WHERE {column} = %s", (value,)).fetchone()["n"] > 0


def checkpoint_count(c, t: str) -> int:
    return sum(c.execute(f"SELECT count(*) AS n FROM {x} WHERE thread_id = %s", (t,)).fetchone()["n"]
               for x in retention.CHECKPOINT_TABLES)


def seed(url):
    """One row on each side of each boundary. Returns the IDs by name."""
    ids = {}
    with db_store.connect(url) as c:
        ids["ck_old"] = thread(c, days=31)                              # checkpoints go, thread stays (< 90)
        ids["ck_new"] = thread(c, days=29)                              # all kept
        ids["ck_waiting"] = thread(c, days=31, status="awaiting_approval")   # kept: waiting for a human
        ids["all_old"] = thread(c, days=91)
        ids["all_old_appr"] = approval(c, ids["all_old"], days=91)
        ids["all_old_ticket"] = ticket(c, ids["all_old"], ids["all_old_appr"], days=91)
        ids["young_ticket_thread"] = thread(c, days=91)
        ids["young_ticket_appr"] = approval(c, ids["young_ticket_thread"], days=91)
        ids["young_ticket"] = ticket(c, ids["young_ticket_thread"], ids["young_ticket_appr"], days=89)
        ids["inside_90"] = thread(c, days=89)
        ids["inside_90_appr"] = approval(c, ids["inside_90"], days=89)
        ids["old_pending_thread"] = thread(c, days=91, status="awaiting_approval")
        ids["old_pending_appr"] = approval(c, ids["old_pending_thread"], days=91, status="pending")
        c.commit()
        # memory: a soft-deleted preference with history, an expired one, one a day inside expiry
        memory.save_preference(c, "u-del", "language", "id")
        memory.save_preference(c, "u-del", "language", "en")             # version 2, one history row
        ids["mem_deleted"] = str(memory.list_preferences(c, "u-del")[0]["memory_id"])
        memory.delete_preference(c, "u-del", "language")
        _, row = memory.save_preference(c, "u-exp", "detail", "brief")
        ids["mem_expired"] = str(row["memory_id"])
        c.execute("UPDATE long_term_memory SET expires_at = now() - interval '1 second' WHERE memory_id = %s",
                  (ids["mem_expired"],))
        _, row = memory.save_preference(c, "u-keep", "detail", "detailed")
        ids["mem_kept"] = str(row["memory_id"])
        c.execute("UPDATE long_term_memory SET expires_at = now() + interval '1 day' WHERE memory_id = %s",
                  (ids["mem_kept"],))
        c.commit()
    return ids


# checkpoints: every thread idle > 30 days and not awaiting approval: ck_old, all_old, young_ticket_thread, inside_90
EXPECTED = {"checkpoint_writes": 4, "checkpoint_blobs": 4, "checkpoints": 4,
            "sandbox_tickets": 1, "approval_requests": 1, "agent_threads": 1,
            "long_term_memory_history": 1, "long_term_memory": 2}


def test_purge_deletes_exactly_the_rows_outside_each_period(url):
    ids = seed(url)
    with psycopg.connect(url, autocommit=True) as conn:
        counts = retention.purge(conn, **P)
    assert counts == EXPECTED
    with db_store.connect(url) as c:
        assert checkpoint_count(c, ids["ck_old"]) == 0 and exists(c, "agent_threads", "thread_id", ids["ck_old"])
        assert checkpoint_count(c, ids["ck_new"]) == 3
        assert checkpoint_count(c, ids["inside_90"]) == 0 and exists(c, "agent_threads", "thread_id", ids["inside_90"])
        assert checkpoint_count(c, ids["ck_waiting"]) == 3                       # awaiting_approval kept
        assert checkpoint_count(c, ids["old_pending_thread"]) == 3
        assert not exists(c, "agent_threads", "thread_id", ids["all_old"])
        assert not exists(c, "approval_requests", "approval_id", ids["all_old_appr"])
        assert not exists(c, "sandbox_tickets", "ticket_id", ids["all_old_ticket"])
        assert exists(c, "sandbox_tickets", "ticket_id", ids["young_ticket"])     # 89 days: kept
        assert exists(c, "approval_requests", "approval_id", ids["young_ticket_appr"])   # its ticket remains
        assert exists(c, "agent_threads", "thread_id", ids["young_ticket_thread"])
        assert exists(c, "approval_requests", "approval_id", ids["inside_90_appr"])
        assert exists(c, "approval_requests", "approval_id", ids["old_pending_appr"])     # pending is never purged
        assert not exists(c, "long_term_memory", "memory_id", ids["mem_deleted"])
        assert not exists(c, "long_term_memory_history", "memory_id", ids["mem_deleted"])
        assert not exists(c, "long_term_memory", "memory_id", ids["mem_expired"])
        assert exists(c, "long_term_memory", "memory_id", ids["mem_kept"])        # one day inside expiry
    with psycopg.connect(url, autocommit=True) as conn:                         # a second run finds nothing
        assert sum(retention.purge(conn, **P).values()) == 0


def test_dry_run_reports_the_same_counts_and_deletes_nothing(url):
    ids = seed(url)
    with psycopg.connect(url, autocommit=True) as conn:
        assert retention.purge(conn, dry_run=True, **P) == EXPECTED
    with db_store.connect(url) as c:
        assert checkpoint_count(c, ids["ck_old"]) == 3
        assert exists(c, "agent_threads", "thread_id", ids["all_old"])
        assert exists(c, "long_term_memory_history", "memory_id", ids["mem_deleted"])
        assert exists(c, "long_term_memory", "memory_id", ids["mem_expired"])


def test_deleted_preference_is_gone_with_its_history_after_retention(url):
    """AC #4: a deleted preference reads as deleted, and after the retention job neither row nor history exists."""
    with db_store.connect(url) as c:
        memory.save_preference(c, "u-a4", "language", "id")
        memory.save_preference(c, "u-a4", "language", "en")
        mid = str(memory.list_preferences(c, "u-a4")[0]["memory_id"])
        assert memory.delete_preference(c, "u-a4", "language")
        assert memory.get_preferences(c, "u-a4") == {}
        c.commit()
    with psycopg.connect(url, autocommit=True) as conn:
        retention.purge(conn, **P)
    with db_store.connect(url) as c:
        assert not exists(c, "long_term_memory", "memory_id", mid)
        assert not exists(c, "long_term_memory_history", "memory_id", mid)


def test_periods_below_one_day_are_refused(url):
    with psycopg.connect(url, autocommit=True) as conn, pytest.raises(ValueError):
        retention.purge(conn, checkpoint_days=0, approval_days=90)


def test_the_app_role_cannot_purge_and_nothing_is_deleted(migrated_db_url, db_roles):
    """AC #27: devguard_app has DELETE on checkpoints (LangGraph needs it) but not on tickets or memory, so the
    purge fails as a whole and its checkpoint deletes roll back with it."""
    with db_store.connect(migrated_db_url) as c:
        t = thread(c, days=31)
        c.commit()
    with psycopg.connect(db_roles.url("app"), autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            retention.purge(conn, **P)
    with db_store.connect(migrated_db_url) as c:
        assert checkpoint_count(c, t) == 3
        c.execute("DELETE FROM checkpoints WHERE thread_id = %s", (t,))         # leave the session schema clean
        c.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (t,))
        c.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (t,))
        c.execute("DELETE FROM agent_threads WHERE thread_id = %s", (t,))
        c.commit()
