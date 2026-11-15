"""scripts/deploy_manifest.py at its real entry point (p9-final spec AC #27)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy_manifest.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=300)


def test_fill_then_check_reports_what_is_missing(tmp_path):
    m = tmp_path / "manifest.yaml"
    r = run("--manifest", str(m), "--fill")
    assert r.returncode == 0, r.stderr
    data = yaml.safe_load(m.read_text(encoding="utf-8"))
    assert data["model"]["quantization"] == "fp8" and len(data["lockfile"]["sha256"]) == 64 and data["images"]
    r = run("--manifest", str(m), "--check")
    assert r.returncode == 1 and "missing/problem: gpu.sku is empty" in r.stdout and "manifest incomplete" in r.stdout


def test_setup_report_is_merged(tmp_path):
    m = tmp_path / "manifest.yaml"
    rep = tmp_path / "setup-x.json"
    rep.write_text(json.dumps({"finished_at": "2026-11-18T10:00:00+00:00", "commit": "abc1234", "fresh_clone": True,
                               "mode": "full", "healthcheck": {"all_pass": True, "checks_passed": 7}}), encoding="utf-8")
    r = run("--manifest", str(m), "--setup-report", str(rep))
    assert r.returncode == 0 and "merged fresh-clone run" in r.stdout
    assert yaml.safe_load(m.read_text(encoding="utf-8"))["fresh_clone"]["commit"] == "abc1234"


def test_no_action_is_usage_error(tmp_path):
    r = run("--manifest", str(tmp_path / "m.yaml"))
    assert r.returncode == 2 and "choose --fill" in r.stderr
