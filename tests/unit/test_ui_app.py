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

SOURCES = [{"document_id": "snap-std", "title": f"SNAP {HOSTILE}", "publisher": "ASPI", "kind": "specification",
            "document_version": "1.0.2", "effective_date": "2024-09", "synthetic": False},
           {"document_id": "synthetic-sop", "title": "SOP (SYNTHETIC)", "publisher": None, "kind": "sop",
            "document_version": "0.1.0", "effective_date": "2026-11-06", "synthetic": True}]
GATE_REPORT = {"generated_at": "2026-11-15T06:55:20+00:00", "verdict": "FAIL", "not_passing": ["g2"],
               "critical_not_passing": [],
               "gates": [{"name": "g1", "kind": "critical", "status": "PASS", "value": 0, "threshold": {"max": 0},
                          "evidence_date": "2026-11-15", "note": ""},
                         {"name": f"g2 {HOSTILE}", "kind": "gate", "status": "FAIL", "value": 0.27,
                          "threshold": {"min": 0.9}, "evidence_date": None, "note": f"note {HOSTILE}"}]}


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

    # p9 ui-refresh (docs/p9-ui-refresh/spec.md Behaviour §8)
    def sources(self):
        self._f("sources")
        return SOURCES

    def release_gate(self):
        self._f("release_gate")
        return GATE_REPORT


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


# --- p9 ui-refresh (docs/p9-ui-refresh/spec.md AC #7-#12) ----------------------------------------------------------

from app.ui import theme  # noqa: E402

NEW_PAGES = ["Overview", "Knowledge base", "Evaluations"]
APPROVER = "Approver (demo-approver)"


def html_bodies(at):
    return [el.proto.body for el in at.get("html")]


def not_in_rich_elements(at, needle):
    """`needle` appears in no element that renders markdown or HTML, nor in any widget label."""
    for kind in MARKDOWNISH:
        assert not any(needle in v for v in values(at, kind)), kind
    assert not any(needle in b for b in html_bodies(at))


@pytest.mark.parametrize("identity", ["Developer (demo-dev)", APPROVER])
@pytest.mark.parametrize("page", NEW_PAGES)
def test_new_pages_render_and_never_show_the_token(identity, page):
    at = app(identity=identity, page=page)
    assert not at.exception, [e.value for e in at.exception]
    assert TOKEN not in everything(at) and not any(TOKEN in b for b in html_bodies(at))
    assert not at.error


def test_nav_keeps_option_values_and_shows_reference_labels():
    at = app(page="Overview")
    radio = at.sidebar.radio(key="page")
    assert radio.options == ["Overview", "Investigations", "Approvals", "Monitoring", "Knowledge base", "Evaluations"]
    assert radio.value == "Overview"


def test_hostile_text_on_new_pages_is_plain_text_only():
    for page in NEW_PAGES:
        at = app(identity=APPROVER, page=page)
        not_in_rich_elements(at, "evil.example")
    kb = app(page="Knowledge base")
    assert any(HOSTILE in v for v in values(kb, "text"))
    ev = app(page="Evaluations")
    shown = "\n".join(values(ev, "text"))
    assert f"g2 {HOSTILE}" in shown and f"note: note {HOSTILE}" in shown


def test_unknown_status_renders_the_unknown_pill(monkeypatch):
    monkeypatch.setattr(Fake, "list_threads", lambda self: [{"thread_id": TID, "status": HOSTILE,
                                                           "created_at": "c", "updated_at": "u"}])
    at = app(page="Overview")
    assert any(">Unknown<" in b for b in html_bodies(at))
    not_in_rich_elements(at, "evil.example")


def test_overview_counts_for_developer_and_approver():
    dev = app(page="Overview")
    stats = next(b for b in html_bodies(dev) if "My threads" in b)
    assert theme.stat("threads", 1) in stats and theme.stat("awaiting", 1) in stats
    assert theme.stat("pending_forbidden", None) in stats and theme.stat("ready", 2, total=2) in stats
    appr = app(identity=APPROVER, page="Overview")
    stats = next(b for b in html_bodies(appr) if "My threads" in b)
    assert theme.stat("pending", 1) in stats


def test_overview_failed_read_shows_a_dash_and_the_envelope():
    err = ApiError(503, "SERVICE_UNAVAILABLE", "the thread and approval store is not available", "cid-9")
    at = app(fake_kwargs={"fail": {"list_threads": err}}, page="Overview")
    stats = next(b for b in html_bodies(at) if "My threads" in b)
    assert theme.stat("threads", None) in stats and theme.stat("awaiting", None) in stats
    assert at.error[0].value == "The request failed." and "code: SERVICE_UNAVAILABLE" in "\n".join(values(at, "text"))


def test_overview_chip_creates_a_thread_and_opens_it():
    at = app(page="Overview")
    at.button(key="chip-0").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.radio(key="page").value == "Threads and chat"
    assert at.session_state["open_thread"] == TID
    assert at.text_area(key="message").value == "Why is my access-token signature rejected?"
    assert f"thread: {TID}" in "\n".join(values(at, "text"))


def test_overview_open_and_library_buttons_switch_page():
    at = app(page="Overview")
    at.button(key=f"ov-open-{TID}").click().run()
    assert at.sidebar.radio(key="page").value == "Threads and chat" and at.session_state["open_thread"] == TID
    at = app(page="Overview")
    at.button(key="ov-library").click().run()
    assert at.sidebar.radio(key="page").value == "Knowledge base"


def test_evaluations_shows_the_fail_verdict_and_one_row_per_gate():
    at = app(page="Evaluations")
    bodies = html_bodies(at)
    assert theme.pill("gate", "FAIL") in bodies
    assert sum(1 for b in bodies if b in (theme.pill("gate", "PASS"), theme.pill("gate", "FAIL"))) == 3  # verdict + 2 rows
    shown = "\n".join(values(at, "text"))
    assert "verdict: FAIL" in shown and "0.27  (threshold >= 0.9)" in shown and "0  (threshold <= 0)" in shown
    stats = next(b for b in bodies if "Gates passing" in b)
    assert theme.stat("gates_passing", 1, total=2) in stats and theme.stat("not_passing", 1) in stats


def test_evaluations_without_a_report_shows_the_empty_state():
    err = ApiError(404, "REPORT_NOT_FOUND", "no release-gate report has been generated yet", "cid-1")
    at = app(fake_kwargs={"fail": {"release_gate": err}}, page="Evaluations")
    assert not at.error
    assert any(v == "No release-gate report yet. Run scripts/release_gate.py." for v in values(at, "info"))


def test_evaluations_other_errors_show_the_envelope():
    err = ApiError(500, "INTERNAL_ERROR", "the request failed", "cid-2")
    at = app(fake_kwargs={"fail": {"release_gate": err}}, page="Evaluations")
    assert at.error[0].value == "The request failed." and "code: INTERNAL_ERROR" in "\n".join(values(at, "text"))


def test_knowledge_base_lists_every_source_with_its_pill():
    at = app(page="Knowledge base")
    bodies = html_bodies(at)
    assert theme.count_head("sources", 2) in bodies
    assert theme.pill("source", "synthetic") in bodies and theme.pill("source", "reference") in bodies
    shown = "\n".join(values(at, "text"))
    assert "snap-std · ASPI" in shown and "synthetic-sop" in shown
