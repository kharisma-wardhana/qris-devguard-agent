"""scripts/bench_report.py at its real entry point (p9-final spec AC #14)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bench_report.py"
FIX = ROOT / "tests" / "fixtures" / "evaluation" / "bench"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


def test_writes_report_with_hardware_and_rows(tmp_path):
    hc = tmp_path / "healthcheck.jsonl"
    hc.write_text(json.dumps({"all_pass": True, "ttft_ms": 600.0, "ts": "t"}) + "\n", encoding="utf-8")
    r = run(str(FIX / "bench-c1.json"), str(FIX / "bench-c4.json"), "--gpu", "RTX PRO 4000", "--driver", "580.95.05",
            "--cuda", "13.0", "--healthcheck", str(hc), "--report-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "concurrency 1: TTFT p50 198.2 / p95 262.9 ms" in r.stdout and "not a gate" in r.stdout
    (rep,) = tmp_path.glob("bench-report-*.json")
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["hardware"]["gpu_sku"] == "RTX PRO 4000" and data["mode"] == "live" and data["notes"] == []
    assert data["source_files"] == ["bench-c1.json", "bench-c4.json"] and data["tunnel_ttft"]["ttft_ms"] == 600.0


def test_missing_field_is_one_line_error(tmp_path):
    bad = tmp_path / "bad.json"
    data = json.loads((FIX / "bench-c1.json").read_text(encoding="utf-8"))
    del data["p95_ttft_ms"]
    bad.write_text(json.dumps(data), encoding="utf-8")
    r = run(str(bad), "--gpu", "g", "--driver", "d", "--cuda", "c", "--report-dir", str(tmp_path))
    assert r.returncode == 1 and r.stderr.startswith("error: bad.json: missing field(s) p95_ttft_ms")
    assert "Traceback" not in r.stderr and not list(tmp_path.glob("bench-report-*.json"))
