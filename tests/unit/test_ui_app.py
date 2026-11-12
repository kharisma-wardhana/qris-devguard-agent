"""p9 Streamlit pages with a fake client (AppTest; docs/p9-demo-ui-alerts/spec.md AC #14, #15, #17, #18).

Skipped where Streamlit isn't installed (the `test` CI job); the `ui-tests` job installs the `ui` extra and runs it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from app.ui.client import ApiError  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "app" / "ui" / "streamlit_app.py")
TOKEN = "eyJTOKEN-SENTINEL-never-shown"
HOSTILE = "[click](https://evil.example/x) <b>bold</b>"
TID = "T" * 24
AID = "0b0b0b0b-1111-4222-8333-444444444444"
APPROVAL = {"approval_id": AID, "thread_id": TID, "status": "pending", "requester_id": "demo-dev",
            "action": {"type": "create_support_ticket", "target": "local sandbox_tickets table",
                       "payload": {"title": f"Escalation: {HOSTILE}", "body": "Question: q", "priority": "normal"}},
            "evidence_ids": ["C1"], "expected_side_effect": "one row", "approver_id": None, "decided_at": None,
            "expires_at": "2026-11-12T05:00:00+00:00", "executed_at": None, "ticket_id": None}
ANSWER = {"summary": f"Invalid signature. {HOSTILE}", "findings": [{"text": HOSTILE, "status": "likely", "citation_ids": ["C1"]}],
          "validation_results": [], "next_steps": [], "confidence": "medium", "limitations": [],
          "sources": [{"citation_id": "C1", "title": "SNAP", "document_id": "d", "document_version": "1",
                       "quote_or_excerpt": HOSTILE, "synthetic": True}]}


COUNTS: dict[str, int] = {}


class Fake:
    def __init__(self, identity, *, fail=None, alerts=None):
        self.identity, self.api_url = identity, "http://127.0.0.1:8000"
        self._token = TOKEN
        self.fail, self._alerts = fail, alerts if alerts is not None else []

    def _f(self, name):
        if self.fail and name in self.fail:
            raise self.fail[name]

    def ready(self):
        return {"status": "ready", "checks": {"mcp": "up", "llm": "up"}}

    def list_threads(self):
        self._f("list_threads")
        COUNTS["list_threads"] = COUNTS.get("list_threads", 0) + 1
        return [{"thread_id": TID, "status": "awaiting_approval", "created_at": "c", "updated_at": "u"}]

    def create_thread(self):
        return TID

    def get_thread(self, tid):
        return {"thread_id": tid, "status": "awaiting_approval", "approval": {"approval_id": AID}}

    def history(self, tid):
        return [{"run_id": "r1", "question": f"q {HOSTILE}", "intent": "signature", "risk_level": "low",
                 "answer": ANSWER, "at": "2026-11-12T03:00:00"}]

    def get_approval(self, aid):
        return APPROVAL

    def pending_approvals(self):
        self._f("pending_approvals")
        return [APPROVAL]

    def alerts(self):
        return {"alerts": self._alerts, "skipped": 0}


def app(fake_kwargs=None, identity="Developer (demo-dev)", page="Threads and chat"):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["_client_factory"] = lambda ident: Fake(ident, **(fake_kwargs or {}))
    at.run()
    at.sidebar.selectbox(key="identity_label").set_value(identity).run()
    at.sidebar.radio(key="page").set_value(page).run()
    return at


MARKDOWNISH = ("markdown", "caption", "title", "header", "subheader", "info", "warning", "error", "success", "toast",
               "expander", "button", "link_button")


def values(at, kind):
    out = []
    for el in at.get(kind):
        for attr in ("value", "label", "body"):
            v = getattr(el, attr, None)
            if isinstance(v, str):
                out.append(v)
    return out


def everything(at):
    return "\n".join(v for kind in (*MARKDOWNISH, "text", "code", "text_area", "text_input") for v in values(at, kind))


@pytest.mark.parametrize("identity", ["Developer (demo-dev)", "Approver (demo-approver)"])
@pytest.mark.parametrize("page", ["Threads and chat", "Approvals", "Alerts"])
def test_every_page_renders_and_never_shows_the_token(identity, page):
    at = app(identity=identity, page=page)
    assert not at.exception, [e.value for e in at.exception]
    assert TOKEN not in everything(at)


def test_untrusted_text_is_only_in_text_elements():
    at = app()
    at.button(key=f"open-{TID}").click().run()
    shown = "\n".join(values(at, "text"))
    assert HOSTILE in shown                                         # shown, literally
    for kind in MARKDOWNISH:
        assert not any("evil.example" in v for v in values(at, kind)), kind


def test_approver_queue_shows_the_payload_as_text():
    at = app(identity="Approver (demo-approver)", page="Approvals")
    assert any(f"approval: {AID}" in v for v in values(at, "text"))
    assert not any("evil.example" in v for kind in MARKDOWNISH for v in values(at, kind))
    assert at.button(key=f"approve-{AID}") and at.button(key=f"reject-{AID}")


def test_developer_sees_no_queue():
    at = app(page="Approvals")
    assert any("approver identities only" in v for v in values(at, "info"))


def test_api_error_shows_code_and_correlation_id_without_values():
    err = ApiError(429, "RATE_LIMITED", "too many requests", "cid-123",
                   [{"field": "message", "reason": "string_too_long"}], retry_after="9")
    at = app(fake_kwargs={"fail": {"list_threads": err}})
    text = "\n".join(values(at, "text"))
    assert "code: RATE_LIMITED" in text and "correlation_id: cid-123" in text
    assert "field: message (string_too_long)" in text and "retry after: 9 s" in text
    assert at.error and at.error[0].value == "The request failed."


def test_alerts_empty_state():
    at = app(identity="Approver (demo-approver)", page="Alerts")
    assert any(v == "No alerts received yet." for v in values(at, "info"))


def test_alerts_listed_with_severity_and_permalink():
    alerts = [{"id": "evt-1", "severity": "ALERT", "title": f"p95 {HOSTILE}", "body": "6100 ms", "window": "5m",
               "received_at": "2026-11-12T03:00:00+00:00", "monitor_id": "m", "from_timestamp": None,
               "to_timestamp": None, "permalink": "http://localhost:3000/project/devguard/monitors/m"}]
    at = app(fake_kwargs={"alerts": alerts}, identity="Approver (demo-approver)", page="Alerts")
    text = "\n".join(values(at, "text"))
    assert f"ALERT (firing) - p95 {HOSTILE}" in text and "alert id: evt-1" in text
    assert not any("evil.example" in v for kind in MARKDOWNISH for v in values(at, kind))


def test_developer_gets_no_alerts():
    at = app(page="Alerts")
    assert any("approver identities only" in v for v in values(at, "info"))


def test_reads_are_cached_across_reruns_and_refresh_clears_them():
    """Each rerun would otherwise re-read threads, thread, history and approval and empty the api_other bucket."""
    COUNTS.clear()
    at = app()
    for _ in range(3):
        at.run()
    assert COUNTS["list_threads"] == 1
    at.button(key="refresh_threads").click().run()
    assert COUNTS["list_threads"] == 2
