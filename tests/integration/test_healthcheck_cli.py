"""Run the health-check CLI at its real entry point against closed ports (spec AC #6, #7)."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "healthcheck.py"
SENTINELS = ["sentinel-vllm-key-aaaa", "pk-lf-sentinel-bbbb", "sk-lf-sentinel-cccc", "sentinel-db-pass-dddd"]


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]  # released on close, so nothing listens there


def test_all_checks_fail_cleanly_when_nothing_runs(tmp_path):
    port = closed_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VLLM_", "LANGFUSE_", "QDRANT_", "DATABASE_"))}
    env.update({
        "VLLM_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "VLLM_API_KEY": SENTINELS[0],
        "LANGFUSE_HOST": f"http://127.0.0.1:{port}",
        "LANGFUSE_PUBLIC_KEY": SENTINELS[1],
        "LANGFUSE_SECRET_KEY": SENTINELS[2],
        "QDRANT_URL": f"http://127.0.0.1:{port}",
        "DATABASE_URL": f"postgresql+psycopg://devguard:{SENTINELS[3]}@127.0.0.1:{port}/devguard",
    })
    report = tmp_path / "report.jsonl"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(tmp_path / "absent.env"),
         "--report", str(report), "--timeout", "2"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    lines = [l for l in result.stdout.splitlines() if l.startswith(("PASS", "FAIL"))]
    assert len(lines) == 7 and all(l.startswith("FAIL") for l in lines), result.stdout
    assert "0/7 checks passed" in result.stdout
    blob = result.stdout + result.stderr + report.read_text(encoding="utf-8")
    for value in SENTINELS:
        assert value not in blob
    assert json.loads(report.read_text(encoding="utf-8"))["all_pass"] is False
