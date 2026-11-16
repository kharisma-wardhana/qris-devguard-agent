"""p9 UI end to end: AppTest drives the real Streamlit page against the real API and MCP subprocesses (fake LLM).
docs/p9-demo-ui-alerts/spec.md AC #13, #16, #17. AppTest is Streamlit's own test driver and stands in for a browser
test (plan.md, step 8); the runbook adds one manual browser pass.

The thread and approval flows need PostgreSQL (-m db, throwaway schema and roles); without it they skip, and the
service-unavailable case still runs.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from app.agent.harness import free_port  # noqa: E402
from app.ui.client import Client  # noqa: E402
from app.validators import keys  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APP = str(ROOT / "app" / "ui" / "streamlit_app.py")
SECRET = secrets.token_hex(24)
DEV, APPROVER = "Developer (demo-dev)", "Approver (demo-approver)"


def _wait(proc, port, what):
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"{what} exited: {proc.stderr.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    proc.kill()
    pytest.fail(f"{what} did not start")


def _start(args, env, port, what):
    proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    _wait(proc, port, what)
    return proc


def _stop(procs):
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def _stack(tmp, extra_env):
    keys_dir = tmp / "keys"
    wanted = yaml.safe_load((ROOT / "knowledge_base/fixtures/signatures/cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)
    mcp_port, api_port = free_port(), free_port()
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env = {**env, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_KEYS_DIR": str(keys_dir), "DEVGUARD_TRACING": "0",
           "QDRANT_URL": f"http://127.0.0.1:{free_port()}", "DEVGUARD_TEST_BACKENDS": "1", **extra_env}
    mcp = _start([str(ROOT / "scripts/mcp_server.py"), "--port", str(mcp_port)], env, mcp_port, "mcp server")
    api = _start([str(ROOT / "scripts/api_server.py"), "--port", str(api_port), "--mcp-url",
                  f"http://127.0.0.1:{mcp_port}/mcp", "--fake-llm"], env, api_port, "api server")
    return f"http://127.0.0.1:{api_port}", [api, mcp]


def page(base, identity, name="Threads and chat"):
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["_client_factory"] = lambda ident: Client(ident, secret=SECRET, api_url=base)
    at.run()
    at.sidebar.selectbox(key="identity_label").set_value(identity).run()
    at.sidebar.radio(key="page").set_value(name).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def texts(at):
    return "\n".join(t.value for t in at.text)


def send(at, example):
    at.selectbox(key="example").set_value(example).run()
    at.get("form_submit_button")[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, texts(at)                     # e.g. a 429 from too many reads per rerun
    return at


# --- without PostgreSQL --------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def nodb(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("uie2e")
    base, procs = _stack(tmp, {"DEVGUARD_DB_DOTENV": str(tmp / "no.env")})
    yield base
    _stop(procs)


def test_without_a_store_the_page_shows_the_envelope(nodb):
    at = page(nodb, DEV)
    assert "mcp: up" in texts(at)
    assert "code: SERVICE_UNAVAILABLE" in texts(at) and "correlation_id: " in texts(at)
    assert at.error[0].value == "The request failed."


# --- with PostgreSQL ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stack(migrated_db_url, db_roles, tmp_path_factory):
    base, procs = _stack(tmp_path_factory.mktemp("uie2edb"), {"DATABASE_URL": migrated_db_url, **db_roles.env()})
    yield base
    _stop(procs)


@pytest.mark.db
def test_developer_creates_a_thread_and_reads_the_answer(stack):
    at = page(stack, DEV)
    at.button(key="new_thread").click().run()
    send(at, "Signature rejected (synthetic RSA fixture)")
    shown = [s.value for s in at.subheader]
    want = ["Summary", "Findings", "Validation results", "Next steps", "Sources", "Confidence and limitations"]
    latest = shown[shown.index("Latest answer") + 1:]
    assert latest[:6] == want
    assert "validate_test_signature -> " in texts(at)
    # the thread is listed and its history has the run
    at.run()
    assert any(b.key and b.key.startswith("open-") for b in at.button)
    assert [e.label for e in at.expander if e.label.startswith("Run ")] == ["Run 1"]


@pytest.mark.db
def test_approval_flow_approve_reject_and_developer_403(stack):
    approval_ids = []
    for _ in range(2):
        dev = page(stack, DEV)                                   # a fresh session per escalation
        dev.button(key="new_thread").click().run()
        send(dev, "Escalate to a sandbox ticket (needs approval)")
        line = next(ln for ln in texts(dev).splitlines() if ln.startswith("approval: "))
        approval_ids.append(line.split()[1])
    first, second = approval_ids

    # the developer cannot decide: 403 envelope
    dev.sidebar.radio(key="page").set_value("Approvals").run()
    dev.text_input(key="manual_aid").set_value(first).run()
    dev.button(key="approve-manual").click().run()
    assert "code: FORBIDDEN" in texts(dev)

    appr = page(stack, APPROVER, "Approvals")
    assert f"approval: {first}" in texts(appr) and f"approval: {second}" in texts(appr)
    appr.button(key=f"approve-{first}").click().run()
    assert not appr.exception
    assert appr.success[0].value == "Decision recorded."
    outcome = texts(appr)
    assert "Approved and executed: sandbox ticket " in outcome and " was created." in outcome
    appr.button(key=f"reject-{second}").click().run()
    assert "Rejected: nothing was created." in texts(appr)
    assert f"approval: {first}" not in texts(appr) and f"approval: {second}" not in texts(appr)


# --- p9 ui-refresh: the new pages against the real API (docs/p9-ui-refresh/spec.md AC #14) -------------------------

import json  # noqa: E402

from app.api import reports  # noqa: E402
from app.rag import manifest  # noqa: E402

GATE_REPORT = {"generated_at": "2026-11-16T00:00:00+00:00", "verdict": "FAIL", "not_passing": ["g2"],
               "critical_not_passing": [], "freshness_days": 14, "reported_not_gated": {},
               "gates": [{"name": "g1", "kind": "critical", "status": "PASS", "value": 0, "threshold": {"max": 0},
                          "evidence": [{"path": "evals/reports/x.json", "sha256": "ef" * 32, "date": "2026-11-15"}],
                          "note": ""},
                         {"name": "g2", "kind": "gate", "status": "FAIL", "value": 0.27, "threshold": {"min": 0.9},
                          "evidence": [], "note": ""}]}


@pytest.fixture(scope="module")
def refresh(tmp_path_factory):
    """No PostgreSQL; the release-gate route reads a temp report (evals/reports/ is git-ignored, so CI has none)."""
    tmp = tmp_path_factory.mktemp("uirefresh")
    (tmp / "release-gate-2026-11-16T000000.json").write_text(json.dumps(GATE_REPORT), encoding="utf-8")
    base, procs = _stack(tmp, {"DEVGUARD_DB_DOTENV": str(tmp / "no.env"), reports.REPORTS_DIR_ENV: str(tmp)})
    yield base
    _stop(procs)


def test_knowledge_base_lists_the_real_manifest(refresh):
    at = page(refresh, DEV, "Knowledge base")
    assert not at.error, texts(at)
    entries = manifest.load_manifest(ROOT / "knowledge_base/manifests/sources.yaml")
    keep = manifest.retrievable_document_ids(entries)
    shown = texts(at)
    for e in entries:
        assert (e["document_id"] in shown) == (e["document_id"] in keep), e["document_id"]
        if e.get("local_path"):
            assert e["local_path"] not in shown


def test_evaluations_shows_the_measured_verdict(refresh):
    at = page(refresh, APPROVER, "Evaluations")
    assert not at.error, texts(at)
    shown = texts(at)
    assert "verdict: FAIL" in shown and "0.27  (threshold >= 0.9)" in shown
    assert "sha256" not in shown and "evals/reports" not in shown


def test_overview_without_a_store_shows_the_envelope_and_still_renders(refresh):
    at = page(refresh, DEV, "Overview")
    assert "code: SERVICE_UNAVAILABLE" in texts(at)                       # threads need PostgreSQL
    bodies = [el.proto.body for el in at.get("html")]
    assert any("My threads" in b and ">—<" in b for b in bodies)          # no figure, never a placeholder number


def test_walking_every_page_stays_inside_the_rate_limit(refresh):
    at = page(refresh, APPROVER, "Overview")
    for name in ["Threads and chat", "Approvals", "Alerts", "Knowledge base", "Evaluations", "Overview",
                 "Knowledge base", "Evaluations"]:
        at.sidebar.radio(key="page").set_value(name).run()
        assert not at.exception, [e.value for e in at.exception]
        assert "code: RATE_LIMITED" not in texts(at), name


@pytest.mark.db
def test_overview_with_a_store_counts_the_developers_threads(stack):
    at = page(stack, DEV)
    at.button(key="new_thread").click().run()
    at.sidebar.radio(key="page").set_value("Overview").run()
    assert not at.error, texts(at)
    bodies = [el.proto.body for el in at.get("html")]
    stats = next(b for b in bodies if "My threads" in b)
    assert ">—<" not in stats.split("Pending approvals")[0]               # threads and awaiting are real counts
    assert any(b.key and b.key.startswith("ov-open-") for b in at.button)
