"""scripts/send_test_alert.py as a subprocess against the real receiver script (docs/p9-demo-ui-alerts/spec.md AC #20)."""

from __future__ import annotations

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
SECRET = "whsec-SYNTHETIC-send-test-0123456789"


def env(**extra):
    e = {k: v for k, v in os.environ.items() if k != rx.SECRET_ENV}
    e.update(extra)
    return e


@pytest.fixture
def receiver(tmp_path):
    port, store = free_port(), tmp_path / "alerts.jsonl"
    proc = subprocess.Popen([sys.executable, "-I", str(ROOT / "app/alerts/receiver.py")], cwd=tmp_path,
                            env=env(**{rx.SECRET_ENV: SECRET, "ALERT_PORT": str(port), "ALERT_STORE": str(store)}),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}{rx.PATH}", store
    proc.terminate()
    proc.wait(timeout=10)


def run(*args, secret=SECRET, tmp=None):
    extra = {rx.SECRET_ENV: secret} if secret is not None else {}
    cmd = [sys.executable, str(ROOT / "scripts/send_test_alert.py"), *args]
    if tmp is not None:
        cmd += ["--env-file", str(tmp / "none.env")]
    return subprocess.run(cmd, cwd=ROOT, env=env(**extra), capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_signed_synthetic_alert_is_stored(receiver):
    url, store = receiver
    r = run("--url", url)
    assert r.returncode == 0, r.stderr
    assert "receiver: HTTP 200" in r.stdout and SECRET not in r.stdout + r.stderr
    rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert rec["id"].startswith("synthetic-") and rec["title"].startswith("SYNTHETIC")
    assert rec["id"] in r.stdout


def test_wrong_secret_is_refused_by_the_receiver(receiver):
    url, store = receiver
    r = run("--url", url, secret="another-secret-value-0000")
    assert r.returncode == 1 and "HTTP 401" in r.stdout and not store.exists()


def test_missing_secret_and_non_loopback_url(tmp_path):
    r = run("--url", "http://127.0.0.1:1/langfuse/alerts", secret=None, tmp=tmp_path)
    assert r.returncode == 1 and rx.SECRET_ENV in r.stderr
    r = run("--url", "http://example.org/langfuse/alerts")
    assert r.returncode == 1 and "loopback" in r.stderr
