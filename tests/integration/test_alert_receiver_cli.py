"""The real receiver script as a subprocess (docs/p9-demo-ui-alerts/spec.md AC #6, #7, #12).

Run exactly as the compose service runs it (`python receiver.py`, standard library only), bound to loopback here.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.agent.harness import free_port
from app.alerts import receiver as rx

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/alerts/receiver.py"
SECRET = "whsec-SYNTHETIC-cli-secret-9876543210"


def env(**extra):
    e = {k: v for k, v in os.environ.items() if k != rx.SECRET_ENV}
    e.update(extra)
    return e


@pytest.fixture
def running(tmp_path):
    port = free_port()
    store = tmp_path / "alerts.jsonl"
    proc = subprocess.Popen([sys.executable, "-I", str(SCRIPT)], cwd=tmp_path,
                            env=env(**{rx.SECRET_ENV: SECRET, "ALERT_PORT": str(port), "ALERT_STORE": str(store)}),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"receiver exited: {proc.stderr.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    yield port, store, proc
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def post(port, body, sig):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", rx.PATH, body=body, headers={"Content-Type": "application/json", "x-langfuse-signature": sig})
    r = c.getresponse()
    return r.status, r.read().decode("utf-8")


def doc(alert_id):
    return json.dumps({"id": alert_id, "timestamp": "2026-11-12T03:00:01.000Z", "type": "monitor-alert",
                       "apiVersion": "v1", "payload": {"monitorId": "mon-1", "severity": "ALERT", "window": "5m",
                                                       "message": {"title": "incident_event", "body": "count 1 >= 1"}}}
                      ).encode("utf-8")


def test_signed_alert_stored_once_bad_signature_refused(running):
    port, store, proc = running
    body = doc("evt-cli-1")
    ts = str(int(time.time()))
    good = f"t={ts},v1={rx.sign(SECRET, ts, body)}"
    assert post(port, body, good) == (200, '{"stored": true}')
    assert post(port, body, good) == (200, '{"stored": false}')
    bad = f"t={ts},v1={rx.sign('another-secret', ts, body)}"
    assert post(port, doc("evt-cli-2"), bad)[0] == 401
    lines = store.read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln)["id"] for ln in lines] == ["evt-cli-1"]
    proc.terminate()
    out, _ = proc.communicate(timeout=10)
    assert "outcome=stored id=evt-cli-1" in out and "outcome=signature_invalid" in out
    assert "count 1" not in out and SECRET not in out and "v1=" not in out


@pytest.mark.parametrize("value", [None, "", "set-me", "replace-me"])
def test_missing_secret_refuses_to_start(tmp_path, value):
    extra = {"ALERT_PORT": str(free_port())}
    if value is not None:
        extra[rx.SECRET_ENV] = value
    r = subprocess.run([sys.executable, "-I", str(SCRIPT)], cwd=tmp_path, env=env(**extra), capture_output=True,
                       text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 2
    assert rx.SECRET_ENV in r.stderr
    if value:
        assert value not in r.stderr.replace(rx.SECRET_ENV, "")
