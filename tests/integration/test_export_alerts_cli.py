"""scripts/export_alerts.py at its real entry point (p9-final spec AC #24)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "export_alerts.py"
sys.path.insert(0, str(ROOT / "scripts"))


def alert(aid, monitor, severity="ALERT"):
    return {"id": aid, "received_at": "2026-11-18T10:00:00+00:00", "timestamp": "2026-11-18T10:00:00Z",
            "monitor_id": monitor, "severity": severity, "title": f"{monitor} fired", "body": "b",
            "from_timestamp": None, "to_timestamp": None, "alert_timestamp": "2026-11-18T10:00:00Z", "window": None,
            "view": None, "permalink": None}


def run(tmp_path, records):
    store = tmp_path / "alerts.jsonl"
    store.write_text("".join(json.dumps(r) + "\n" for r in records) + "not json\n", encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "--store", str(store), "--report-dir", str(tmp_path)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)


def report(tmp_path):
    (rep,) = tmp_path.glob("alerts-*.json")
    return json.loads(rep.read_text(encoding="utf-8"))


def test_exports_only_the_real_alert(tmp_path):
    r = run(tmp_path, [alert("synthetic-ab12cd34ef56", "synthetic-test-monitor"), alert("evt_real_1", "cm-monitor-1")])
    assert r.returncode == 0, r.stderr
    data = report(tmp_path)
    assert [a["id"] for a in data["real"]] == ["evt_real_1"]
    assert data["synthetic_excluded"] == 1 and data["store_lines_skipped"] == 1
    assert data["real_alert_fired"] is True and data["mode"] == "live"


def test_synthetic_only_is_not_a_fired_alert(tmp_path):
    r = run(tmp_path, [alert("synthetic-ab12cd34ef56", "synthetic-test-monitor")])
    assert r.returncode == 1
    data = report(tmp_path)
    assert data["real"] == [] and data["real_alert_fired"] is False


def test_real_delivery_in_ok_state_is_not_fired(tmp_path):
    run(tmp_path, [alert("evt_real_2", "cm-monitor-1", severity="OK")])
    assert report(tmp_path)["real_alert_fired"] is False


def test_is_synthetic():
    from export_alerts import is_synthetic
    assert is_synthetic({"id": "synthetic-x", "monitor_id": "m"}) and is_synthetic({"id": "e", "monitor_id": "synthetic-m"})
    assert not is_synthetic({"id": "evt", "monitor_id": "cm-1"})
