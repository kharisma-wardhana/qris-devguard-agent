"""QRIS DevGuard demo UI (docs/p9-demo-ui-alerts/spec.md Behaviour §1-§6; AC #13-#18).

Run through scripts/ui_server.py (loopback only, usage statistics off). The page talks to /api/v1 through
app.ui.client only. Rules for this file:
- Text that comes from the API (questions, answers, excerpts, approval payloads, alerts) is shown with st.text or
  st.code only, never st.markdown, and never in a widget label (labels render markdown). Headings and labels
  are fixed words from this file or app.ui.render.
- The token never reaches the page: the client object keeps it.
Tests inject a client factory through st.session_state["_client_factory"] (AppTest); the default factory reads
the signing secret the same way scripts/mint_dev_token.py does.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.agent import config  # noqa: E402
from app.ui import render  # noqa: E402
from app.ui.client import ApiError, Client, Identity, identities, is_approver, parse_context  # noqa: E402

API_URL_ENV = "DEVGUARD_UI_API_URL"          # set by scripts/ui_server.py --api-url (tests); default configs/agent.yaml
SIG_CTX = {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                               "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                               "test_case_ref": "sig-at-wrong-separator"}}
EXAMPLES: dict[str, tuple[str, dict[str, Any] | None]] = {
    "(write your own)": ("", None),
    "Signature rejected (synthetic RSA fixture)": ("Why is my access-token signature rejected?", SIG_CTX),
    "Response code lookup": ("What does response code 4017300 mean?", {"api_family": "snap", "response_code": "4017300"}),
    "Escalate to a sandbox ticket (needs approval)":
        ("My access-token signature keeps failing, please create a ticket for this.", SIG_CTX),
}
BADGE_COLOR = {"ALERT": "red", "WARNING": "orange", "OK": "green"}


def default_factory(identity: Identity) -> Client:
    from app.mcp_server.auth import load_secret
    return Client(identity, secret=load_secret(), api_url=os.environ.get(API_URL_ENV) or None)


def client() -> Client:
    ss = st.session_state
    ident: Identity = ss["identity"]
    if ss.get("_client_for") != ident.sub:
        factory = ss.get("_client_factory") or default_factory
        ss["_client"] = factory(ident)
        ss["_client_for"] = ident.sub
        for key in ("open_thread", "last_run", "decision", "_reads"):
            ss.pop(key, None)
    return ss["_client"]


def show_error(err: ApiError) -> None:
    st.error("The request failed.")
    lines = [f"code: {err.code}", f"message: {err.message}"]
    if err.correlation_id:
        lines.append(f"correlation_id: {err.correlation_id}")
    lines += [f"field: {f['field']} ({f['reason']})" for f in err.field_errors]
    if err.retry_after:
        lines.append(f"retry after: {err.retry_after} s")
    st.text("\n".join(lines))


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ApiError as err:
        show_error(err)
        return None


def read(name: str, fn, *args):
    """A read through the per-session cache. Each rerun would otherwise make several API reads, and a few clicks
    would empty the subject's api_other bucket (429). Errors are shown and never cached."""
    cache = st.session_state.setdefault("_reads", {})
    key, now = (name, *args), time.monotonic()
    hit = cache.get(key)
    if hit and now - hit[0] < config.agent()["ui"]["read_cache_seconds"]:
        return hit[1]
    value = call(fn, *args)
    if value is not None:
        cache[key] = (now, value)
    return value


def invalidate() -> None:
    st.session_state["_reads"] = {}


def write(fn, *args, **kwargs):
    """A write: always sent, and every cached read is dropped afterwards."""
    try:
        return call(fn, *args, **kwargs)
    finally:
        invalidate()


def show_sections(sections: list[tuple[str, list[str]]]) -> None:
    for heading, lines in sections:
        st.subheader(heading)
        st.text("\n".join(lines))


# --- sidebar -------------------------------------------------------------------------------------------------

def sidebar() -> str:
    ids = identities()
    labels = {i.label: i for i in ids}
    with st.sidebar:
        st.header("QRIS DevGuard demo")
        st.caption("Synthetic data only. Local demo; not a product UI.")
        choice = st.selectbox("Demo identity", list(labels), key="identity_label")
        st.session_state["identity"] = labels[choice]
        c = client()
        st.text(f"role: {labels[choice].role}\nAPI: {c.api_url}")
        if st.button("Check API status", key="ready") or "ready_checks" not in st.session_state:
            ready = call(c.ready)
            st.session_state["ready_checks"] = (ready or {}).get("checks", {})
        checks = st.session_state["ready_checks"]
        st.text("\n".join(f"{k}: {v}" for k, v in checks.items()) or "status unknown")
        return st.radio("Page", ["Threads and chat", "Approvals", "Alerts"], key="page")


# --- threads and chat --------------------------------------------------------------------------------------------

def pick_example() -> None:
    message, ctx = EXAMPLES[st.session_state["example"]]
    st.session_state["message"] = message
    st.session_state["context_text"] = "" if ctx is None else json.dumps(ctx, indent=2)


def threads_page() -> None:
    c = client()
    st.title("Threads and chat")
    left, right = st.columns([1, 2])
    with left:
        if st.button("Refresh", key="refresh_threads"):
            invalidate()
        if st.button("New thread", key="new_thread"):
            tid = write(c.create_thread)
            if tid:
                st.session_state["open_thread"] = tid
                st.session_state.pop("last_run", None)
        st.subheader("My threads")
        threads = read("threads", c.list_threads) or []
        if not threads:
            st.text("No threads yet.")
        for t in threads:
            label = f"{render.short_id(t['thread_id'])}  {t['status']}"      # server IDs and status enum only
            if st.button(label, key=f"open-{t['thread_id']}"):
                st.session_state["open_thread"] = t["thread_id"]
                st.session_state.pop("last_run", None)
    with right:
        tid = st.session_state.get("open_thread")
        if not tid:
            st.info("Create a thread or open one from the list.")
            return
        thread_view(c, tid)


def thread_view(c: Client, tid: str) -> None:
    thread = read("thread", c.get_thread, tid)
    if thread is None:
        return
    st.subheader("Open thread")
    st.text(f"thread: {tid}\nstatus: {thread['status']}")
    runs = read("history", c.history, tid) or []
    for n, run in enumerate(runs, 1):
        with st.expander(f"Run {n}", expanded=False):
            st.text(f"question: {run.get('question') or ''}\nintent: {run.get('intent')}  risk: {run.get('risk_level')}"
                    f"\nrun: {run.get('run_id')}  at: {run.get('at')}")
            show_sections(render.answer_sections(run.get("answer")))
    if thread["status"] == "awaiting_approval" and thread.get("approval"):
        approval_id = thread["approval"].get("approval_id")
        view = read("approval", c.get_approval, approval_id) if approval_id else None
        st.warning("This thread is waiting for an approval.")
        if view:
            st.text("\n".join(render.approval_lines(view)))
            if view["status"] != "pending" and st.button("Resume", key="resume"):
                out = write(c.resume, tid)
                if out:
                    st.session_state["last_run"] = out
    last = st.session_state.get("last_run")
    if last and last.get("thread_id") == tid:
        st.subheader("Latest answer")
        st.text(f"run: {last.get('run_id')}  status: {last.get('status')}  intent: {last.get('intent')}  "
                f"risk: {last.get('risk_level')}")
        show_sections(render.answer_sections(last.get("answer")))
        if last.get("approval"):
            st.subheader("Proposed action, waiting for approval")
            st.text("\n".join(render.approval_lines(last["approval"])))
    chat_form(c, tid, disabled=thread["status"] in ("awaiting_approval", "running"))


def chat_form(c: Client, tid: str, disabled: bool) -> None:
    st.subheader("Send a message")
    st.selectbox("Example (synthetic)", list(EXAMPLES), key="example", on_change=pick_example)
    with st.form("send", clear_on_submit=False):
        message = st.text_area("Message", key="message", max_chars=config.agent()["api"]["max_message_chars"])
        with st.expander("Context (JSON object, optional)"):
            context_text = st.text_area("Context", key="context_text", label_visibility="collapsed")
        sent = st.form_submit_button("Send", disabled=disabled)
    if disabled:
        st.caption("A thread waiting for approval takes no new messages.")
    if sent:
        if not message.strip():
            st.error("Write a message first.")
            return
        try:
            ctx = parse_context(context_text)
        except ValueError:
            st.error("Context must be a JSON object.")
            return
        with st.spinner("The agent is working..."):
            out = write(c.send, tid, message, ctx)
        if out:
            st.session_state["last_run"] = out
            st.rerun()


# --- approvals ---------------------------------------------------------------------------------------------------

def approvals_page() -> None:
    c = client()
    st.title("Approvals")
    decision = st.session_state.pop("decision", None)
    if decision:
        st.success("Decision recorded.")
        st.text(render.decision_outcome(decision))
        show_sections(render.answer_sections((decision.get("run") or {}).get("answer")))
    if is_approver(c.identity):
        pending = read("pending", c.pending_approvals)
        if pending is not None and not pending:
            st.info("No pending approvals.")
        for view in pending or []:
            aid = view["approval_id"]
            with st.container(border=True):
                st.text("\n".join(render.approval_lines(view)))
                decide_controls(c, aid)
    else:
        st.info("Pending approvals are listed for approver identities only.")
    with st.expander("Decide by approval ID"):
        aid = st.text_input("Approval ID", key="manual_aid").strip()
        if aid:
            decide_controls(c, aid, key="manual")


def decide_controls(c: Client, aid: str, key: str | None = None) -> None:
    k = key or aid
    reason = st.text_input("Reason (optional)", key=f"reason-{k}", max_chars=500)
    a, r = st.columns(2)
    choice = "approve" if a.button("Approve", key=f"approve-{k}") else "reject" if r.button("Reject", key=f"reject-{k}") \
        else None
    if choice:
        with st.spinner("Recording the decision and resuming the run..."):
            out = write(c.decide, aid, choice, reason or None)
        if out:
            st.session_state["decision"] = out
            st.rerun()


# --- alerts ------------------------------------------------------------------------------------------------------

def alerts_page() -> None:
    c = client()
    st.title("Alerts")
    st.caption("Langfuse alerts received by the local webhook receiver. Receiving an alert never triggers anything.")
    if not is_approver(c.identity):
        st.info("Alerts are shown to approver identities only.")
        return
    auto = st.toggle("Auto-refresh", key="auto_refresh")
    st.button("Refresh", key="refresh_alerts")
    every = config.agent()["ui"]["refresh_seconds"] if auto else None

    @st.fragment(run_every=every)
    def feed() -> None:
        data = call(c.alerts)
        if data is None:
            return
        alerts = data["alerts"]
        seen: set[str] = st.session_state.setdefault("seen_alerts", set())
        fresh = [a for a in alerts if a["id"] not in seen and a["severity"] == "ALERT"]
        if seen and fresh:
            st.toast(f"{len(fresh)} new alert(s) firing")
        seen.update(a["id"] for a in alerts)
        if data.get("skipped"):
            st.caption(f"{data['skipped']} stored record(s) failed validation and are not shown.")
        if not alerts:
            st.info("No alerts received yet.")
            return
        for a in alerts:
            header, lines = render.alert_lines(a)
            with st.container(border=True):
                st.badge(render.SEVERITY_LABEL.get(a["severity"], "UNKNOWN"), color=BADGE_COLOR.get(a["severity"], "gray"))
                st.text(header)
                st.text("\n".join(lines))
                if a.get("permalink"):
                    st.link_button("Open in Langfuse", a["permalink"])
    feed()


def main() -> None:
    st.set_page_config(page_title="QRIS DevGuard demo", layout="wide")
    page = sidebar()
    if page == "Approvals":
        approvals_page()
    elif page == "Alerts":
        alerts_page()
    else:
        threads_page()


main()
