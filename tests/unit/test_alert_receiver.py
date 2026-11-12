"""p9 alert receiver (docs/p9-demo-ui-alerts/spec.md Behaviour §5, Data; AC #6-#11)."""

from __future__ import annotations

import ast
import http.client
import json
import logging
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path

import pytest

from app.agent import config
from app.alerts import receiver as rx

SECRET = "whsec-SYNTHETIC-test-secret-0123456789"


def alert(alert_id="evt-1", severity="ALERT", **payload):
    p = {"monitorId": "mon-1", "projectId": "proj-SECRETISH", "permalink": "http://localhost:3000/project/p/monitors/m",
         "message": {"title": "p95 latency crossed alert threshold", "body": "p95 latency is 6100 ms (threshold: 3000 ms)"},
         "severity": severity, "timestamp": "2026-11-12T03:00:00.000Z", "fromTimestamp": "2026-11-12T02:55:00.000Z",
         "toTimestamp": "2026-11-12T03:00:00.000Z", "view": "observations", "filters": [{"column": "name"}],
         "window": "5m"}
    p.update(payload)
    return {"id": alert_id, "timestamp": "2026-11-12T03:00:01.000Z", "type": "monitor-alert", "apiVersion": "v1",
            "payload": p}


def raw(doc):
    return json.dumps(doc).encode("utf-8")


def header(body: bytes, t=None, secret=SECRET):
    ts = str(int(time.time()) if t is None else t)
    return f"t={ts},v1={rx.sign(secret, ts, body)}"


# --- pins ------------------------------------------------------------------------------------------------------

def test_limits_match_configs_agent_yaml():
    a = config.agent()["alerts"]
    assert (rx.MAX_BODY_BYTES, rx.MAX_CLOCK_SKEW_SECONDS, rx.MAX_RECORDS, rx.TITLE_MAX_CHARS, rx.BODY_MAX_CHARS,
            rx.LANGFUSE_BASE_URL, rx.SECRET_ENV) == (a["max_body_bytes"], a["max_clock_skew_seconds"], a["max_records"],
                                                     a["title_max_chars"], a["body_max_chars"], a["langfuse_base_url"],
                                                     a["secret_env"])


def test_receiver_imports_standard_library_only():
    tree = ast.parse(Path(rx.__file__).read_text(encoding="utf-8"))
    mods = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
           {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods - {"__future__"} <= set(sys.stdlib_module_names), mods


# --- signature and freshness (AC #7) ---------------------------------------------------------------------------

def test_valid_signature_passes():
    body = raw(alert())
    rx.verify_signature(header(body), body, SECRET, time.time())


@pytest.mark.parametrize("hdr,code", [
    (None, "signature_missing"), ("", "signature_missing"), ("v1=abc", "signature_malformed"),
    ("t=abc,v1=" + "0" * 64, "signature_malformed"), ("t=1,v1=xyz", "signature_malformed"),
    ("t=1700000000,v1=" + "0" * 64, "signature_invalid"), ("x" * 300, "signature_missing")])
def test_bad_signatures(hdr, code):
    with pytest.raises(rx.Refused) as e:
        rx.verify_signature(hdr, raw(alert()), SECRET, time.time())
    assert e.value.status == 401 and e.value.code == code


def test_wrong_secret_and_tampered_body():
    body = raw(alert())
    for h, b in ((header(body, secret="other-secret"), body), (header(body), body + b" ")):
        with pytest.raises(rx.Refused) as e:
            rx.verify_signature(h, b, SECRET, time.time())
        assert e.value.code == "signature_invalid"


@pytest.mark.parametrize("offset", [-601, 601, -3600])
def test_stale_or_future_timestamp(offset):
    body, now = raw(alert()), time.time()
    with pytest.raises(rx.Refused) as e:
        rx.verify_signature(header(body, t=int(now) + offset), body, SECRET, now)
    assert e.value.code == "signature_stale"


def test_millisecond_timestamp_is_accepted_within_window():
    body, now = raw(alert()), time.time()
    rx.verify_signature(header(body, t=int(now * 1000)), body, SECRET, now)


# --- shape and allowlist (AC #8, #9) -----------------------------------------------------------------------------

def test_record_keeps_allowlisted_fields_only():
    rec = rx.parse(raw(alert()), "2026-11-12T03:00:02+00:00")
    assert set(rec) == {"id", "received_at", "timestamp", "monitor_id", "severity", "title", "body", "from_timestamp",
                        "to_timestamp", "alert_timestamp", "window", "view", "permalink"}
    assert "SECRETISH" not in json.dumps(rec) and "filters" not in json.dumps(rec)
    assert rec["severity"] == "ALERT" and rec["permalink"].startswith("http://localhost:3000/")


def test_truncation_and_control_characters():
    rec = rx.parse(raw(alert(message={"title": "T" * 500 + "\x1b[31m", "body": "line1\nline2\x07" + "B" * 5000})), "now")
    assert len(rec["title"]) == 200 and len(rec["body"]) == 1000
    assert "\x1b" not in rec["title"] and "\x07" not in rec["body"] and "line1\nline2" in rec["body"]


@pytest.mark.parametrize("link", ["https://evil.example/x", "http://localhost:3000.evil.example/x", "javascript:alert(1)",
                                  "http://localhost:3000/ok path", 42])
def test_permalink_outside_langfuse_is_dropped(link):
    assert rx.parse(raw(alert(permalink=link)), "now")["permalink"] is None


@pytest.mark.parametrize("doc,code", [
    (b"not json", "invalid_json"), (b"\xff\xfe", "invalid_json"), (b"[]", "invalid_shape"),
    ({"id": "e", "type": "prompt-version", "apiVersion": "v1", "payload": {}}, "unsupported_type"),
    ({**alert(), "apiVersion": "v2"}, "unsupported_type"),
    (alert(severity="CRITICAL"), "unknown_severity"),
    (alert(alert_id="bad id with spaces"), "invalid_shape"),
    (alert(monitorId=None), "invalid_shape"),
    (alert(message="flat string"), "invalid_shape"),
    (alert(message={"title": 1, "body": "b"}), "invalid_shape"),
    (alert(window="<script>"), "invalid_shape")])
def test_off_shape_is_422(doc, code):
    with pytest.raises(rx.Refused) as e:
        rx.parse(doc if isinstance(doc, bytes) else raw(doc), "now")
    assert e.value.status == 422 and e.value.code == code


# --- store (AC #6, #10) -----------------------------------------------------------------------------------------

def test_store_dedupes_by_id(tmp_path):
    s = rx.Store(tmp_path / "a" / "alerts.jsonl")
    rec = rx.parse(raw(alert()), "now")
    assert s.add(rec) is True and s.add(rec) is False
    assert len((tmp_path / "a" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_store_keeps_newest_500(tmp_path):
    s = rx.Store(tmp_path / "alerts.jsonl")
    for i in range(505):
        s.add(rx.parse(raw(alert(alert_id=f"evt-{i}")), "now"))
    ids = [json.loads(ln)["id"] for ln in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ids) == 500 and ids[0] == "evt-5" and ids[-1] == "evt-504"
    assert not list(tmp_path.glob(".alerts-*"))                     # no temp file left behind


# --- over HTTP, in-process (AC #6, #7, #8, #11) --------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    store = rx.Store(tmp_path / "alerts.jsonl")
    srv = HTTPServer(("127.0.0.1", 0), rx.handler_for(store, SECRET))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1], tmp_path / "alerts.jsonl"
    srv.shutdown()
    srv.server_close()


def post(port, body: bytes, headers=None, path=rx.PATH):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
    r = c.getresponse()
    return r.status, r.read().decode("utf-8")


def test_signed_alert_is_stored_once(server):
    port, path = server
    body = raw(alert())
    assert post(port, body, {"x-langfuse-signature": header(body)}) == (200, '{"stored": true}')
    assert post(port, body, {"x-langfuse-signature": header(body)}) == (200, '{"stored": false}')
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_refusals_store_nothing_and_echo_nothing(server, caplog):
    port, path = server
    body = raw(alert(message={"title": "PLANTED-TITLE", "body": "PLANTED-BODY"}))
    good = header(body)
    caplog.set_level(logging.INFO, logger="devguard.alert_receiver")
    wrong = good[:-1] + ("0" if good[-1] != "0" else "1")
    cases = [
        (body, {}, 401), (body, {"x-langfuse-signature": wrong}, 401),
        (body, {"x-langfuse-signature": header(body, t=int(time.time()) - 3600)}, 401),
        (b"{}", {"x-langfuse-signature": header(b"{}")}, 422),
        (b"x" * (rx.MAX_BODY_BYTES + 1), {"x-langfuse-signature": good}, 413),
    ]
    for b, h, status in cases:
        code, text = post(port, b, h)
        assert code == status
        assert "PLANTED" not in text and "v1=" not in text
    assert not path.exists()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "PLANTED" not in logged and good.split("v1=")[1] not in logged and "v1=" not in logged
    # a stored alert logs its id and severity, still no text
    caplog.clear()
    assert post(port, body, {"x-langfuse-signature": header(body)})[0] == 200
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "id=evt-1" in logged and "severity=ALERT" in logged and "PLANTED" not in logged


def test_other_paths_and_methods(server):
    port, _ = server
    assert post(port, b"{}", path="/other")[0] == 404
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", rx.PATH)
    assert c.getresponse().status == 405


def test_oversized_body_is_refused_before_reading(server):
    """413 from Content-Length alone: the client sends headers only and never the body."""
    port, _ = server
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST", rx.PATH)
    c.putheader("Content-Length", str(rx.MAX_BODY_BYTES * 100))
    c.endheaders()
    assert c.getresponse().status == 413


def test_chunked_body_is_refused(server):
    port, _ = server
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", rx.PATH, body=iter([b"{}"]), encode_chunked=True, headers={"Transfer-Encoding": "chunked"})
    assert c.getresponse().status == 411
