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
