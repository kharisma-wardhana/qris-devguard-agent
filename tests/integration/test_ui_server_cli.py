"""scripts/ui_server.py as a subprocess (docs/p9-demo-ui-alerts/spec.md Behaviour §7; AC #19)."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("streamlit")

from app.agent.harness import free_port  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ui_server.py"
SECRET = secrets.token_hex(24)


def run_env(**extra):
    return {**os.environ, "MCP_AUTH_SECRET": SECRET, **extra}


def test_serves_on_loopback_with_usage_stats_off():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--port", str(port), "--api-url",
                             f"http://127.0.0.1:{free_port()}"], cwd=ROOT, env=run_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    try:
        deadline, up = time.time() + 90, False
        while time.time() < deadline and not up:
            if proc.poll() is not None:
                pytest.fail(f"ui server exited: {proc.stdout.read()}")
            try:
                up = httpx.get(f"http://127.0.0.1:{port}/_stcore/health", timeout=2).text == "ok"
            except httpx.HTTPError:
                time.sleep(0.5)
        assert up, "streamlit did not become healthy"
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    assert f"demo UI on http://127.0.0.1:{port}" in out and "usage statistics off" in out
    assert SECRET not in out


def test_command_line_is_loopback_and_stats_off():
    sys.path.insert(0, str(ROOT / "scripts"))
    import ui_server
    cmd = ui_server.command("127.0.0.1", 8501)
    pairs = dict(zip(cmd, cmd[1:]))
    assert pairs["--server.address"] == "127.0.0.1" and pairs["--browser.gatherUsageStats"] == "false"
    assert pairs["--server.headless"] == "true" and pairs["--server.enableXsrfProtection"] == "true"


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://" + ".".join(["10", "0", "0", "5"]) + ":8000",
                                 "https://example.org"])
def test_non_loopback_api_url_is_refused(url):
    r = subprocess.run([sys.executable, str(SCRIPT), "--api-url", url], cwd=ROOT, env=run_env(), capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 1 and "loopback" in r.stderr


def test_missing_signing_secret_is_refused(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "MCP_AUTH_SECRET"}
    # A too-short secret in the environment wins over .env and is refused by name, never echoed.
    r = subprocess.run([sys.executable, str(SCRIPT), "--port", str(free_port()), "--api-url",
                        f"http://127.0.0.1:{free_port()}"], cwd=ROOT, env={**env, "MCP_AUTH_SECRET": "short"},
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 1 and "MCP_AUTH_SECRET" in r.stderr and "short" not in r.stderr.replace("shorter", "")


def test_non_loopback_ui_host_is_refused(monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "scripts"))
    import ui_server

    from app.agent import config
    real = config.agent()
    monkeypatch.setattr(config, "agent", lambda: {**real, "ui": {**real["ui"], "host": "0" + ".0.0.0"}})
    assert ui_server.main([]) == 1
    assert "loopback" in capsys.readouterr().err


def test_socket_is_not_reachable_on_other_interfaces():
    """Streamlit binds the configured address only: a listener on 127.0.0.1 refuses the machine's LAN address."""
    lan = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((".".join(["192", "0", "2", "1"]), 9))   # TEST-NET-1; UDP connect sends nothing
            lan = s.getsockname()[0]
    except OSError:
        pytest.skip("no non-loopback interface")
    if lan.startswith("127."):
        pytest.skip("no non-loopback interface")
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--port", str(port), "--api-url",
                             f"http://127.0.0.1:{free_port()}"], cwd=ROOT, env=run_env(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/_stcore/health", timeout=2).text == "ok":
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        with pytest.raises(OSError):
            socket.create_connection((lan, port), timeout=2).close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
