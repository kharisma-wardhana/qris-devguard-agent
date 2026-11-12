"""p9 alert reader and GET /api/v1/alerts (docs/p9-demo-ui-alerts/spec.md Behaviour §6; AC #4)."""

from __future__ import annotations

import json
import secrets

import pytest
from fastapi.testclient import TestClient

from app.agent import config
from app.alerts import store as alert_store
from app.api.app import create_app
from app.mcp_server import auth

API_AUD = config.agent()["api"]["audience"]
SECRET = secrets.token_hex(24)
PLANTED = "S3cr3tValueSYN"


def rec(i, **kw):
    r = {"id": f"evt-{i}", "received_at": f"2026-11-12T03:{i % 60:02d}:00+00:00", "timestamp": None,
         "monitor_id": "mon-1", "severity": "ALERT", "title": f"alert {i}", "body": "p95 latency 6100 ms",
         "from_timestamp": None, "to_timestamp": None, "alert_timestamp": None, "window": "5m",
         "view": "observations", "permalink": "http://localhost:3000/project/p/monitors/m"}
    r.update(kw)
    return json.dumps(r)


def write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_missing_file_is_empty(tmp_path):
    assert alert_store.read_alerts(tmp_path / "none.jsonl") == ([], 0)


def test_newest_first_and_capped(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, [rec(i) for i in range(60)])
    alerts, skipped = alert_store.read_alerts(p)
    assert skipped == 0 and len(alerts) == 50
    assert alerts[0]["id"] == "evt-59" and alerts[-1]["id"] == "evt-10"


def test_invalid_lines_are_skipped_and_counted(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, [rec(1), "not json", json.dumps({"id": "evt-x"}), rec(2, severity="CRITICAL"),
              rec(3, extra="field"), rec(4, title="T" * 201), rec(5, id="bad id"), "", rec(6)])
    alerts, skipped = alert_store.read_alerts(p)
    assert [a["id"] for a in alerts] == ["evt-6", "evt-1"] and skipped == 6


def test_secret_in_text_is_redacted(tmp_path):
    p = tmp_path / "a.jsonl"
    write(p, [rec(1, title=f'password: "{PLANTED}"', body=f'client_secret: "{PLANTED}" in body')])
    alerts, _ = alert_store.read_alerts(p)
    assert PLANTED not in json.dumps(alerts)


@pytest.mark.parametrize("link", ["https://evil.example/x", "http://localhost:3000.evil.example/", "http://localhost:3000/a b"])
def test_permalink_rule_is_reapplied(tmp_path, link):
    p = tmp_path / "a.jsonl"
    write(p, [rec(1, permalink=link)])
    assert alert_store.read_alerts(p)[0][0]["permalink"] is None


async def up() -> bool:
    return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = tmp_path / "alerts.jsonl"
    write(p, [rec(1), rec(2, title=f'secret: "{PLANTED}"'), "garbage"])
    monkeypatch.setenv(alert_store.STORE_ENV, str(p))
    return TestClient(create_app(agent=None, secret=SECRET, mcp_ping=up, llm_ping=up))


def hdr(role):
    return {"Authorization": f"Bearer {auth.mint('u-1', role, secret=SECRET, audience=API_AUD)}"}


@pytest.mark.parametrize("role", ["support_engineer", "admin"])
def test_route_for_approvers(client, role):
    r = client.get("/api/v1/alerts", headers=hdr(role))
    assert r.status_code == 200
    body = r.json()
    assert [a["id"] for a in body["alerts"]] == ["evt-2", "evt-1"] and body["skipped"] == 1
    assert PLANTED not in r.text


@pytest.mark.parametrize("role", ["developer", "kb_maintainer"])
def test_route_forbidden_for_others(client, role):
    r = client.get("/api/v1/alerts", headers=hdr(role))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


def test_route_needs_a_token(client):
    assert client.get("/api/v1/alerts").status_code == 401


def test_route_with_no_store_file(tmp_path, monkeypatch):
    monkeypatch.setenv(alert_store.STORE_ENV, str(tmp_path / "missing.jsonl"))
    c = TestClient(create_app(agent=None, secret=SECRET, mcp_ping=up, llm_ping=up))
    assert c.get("/api/v1/alerts", headers=hdr("admin")).json() == {"alerts": [], "skipped": 0}
