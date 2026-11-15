"""scripts/judge_answers.py at its real entry point with the fake batch client (p9-final spec AC #10-#12).

The real Anthropic client is never constructed here: every run passes --fake-client, which the script refuses unless
DEVGUARD_TEST_BACKENDS=1, so CI never reaches Anthropic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "judge_answers.py"
SMALL = ROOT / "tests" / "fixtures" / "evaluation" / "golden_small.yaml"


def run(*args, test_backends=True):
    env = {**os.environ}
    env.pop("ANTHROPIC_API_KEY", None)
    if test_backends:
        env["DEVGUARD_TEST_BACKENDS"] = "1"
    else:
        env.pop("DEVGUARD_TEST_BACKENDS", None)
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120)


def answers_file(tmp_path):
    recs = []
    for iid, behaviour, cites in (("ga-001", "answer", ["c1"]), ("ga-002", "refuse", []), ("ga-003", "not_found", [])):
        recs.append({"id": iid, "expected_behaviour": behaviour, "observed_behaviour": behaviour, "behaviour_match": True,
                     "answer": {"summary": "s", "findings": [], "validation_results": [], "next_steps": [],
                                "limitations": []},
                     "citations": [{"citation_id": c, "document_id": "d", "section": "1.3", "excerpt": "e"} for c in cites]})
    path = tmp_path / "answers-ci-x.json"
    path.write_text(json.dumps({"mode": "ci", "dataset_version": "0.0.1", "records": recs}), encoding="utf-8")
    return path


def labels_file(tmp_path, grades):
    path = tmp_path / "cal.yaml"
    path.write_text(yaml.safe_dump({"items": [{"id": i, "developer_supported": g} for i, g in grades.items()]}),
                    encoding="utf-8")
    return path


def test_fake_run_writes_report_with_metrics_and_calibration(tmp_path):
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps({"ga-003": {"type": "errored"}}), encoding="utf-8")
    cal = labels_file(tmp_path, {"ga-001": True, "ga-002": True, "ga-003": True})
    r = run(str(answers_file(tmp_path)), "--dataset", str(SMALL), "--calibration", str(cal),
            "--report-dir", str(tmp_path), "--fake-client", str(entries))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "submitted 3 requests as batch fake-batch-1" in r.stdout
    (report,) = tmp_path.glob("judge-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["model"] == "claude-opus-5-5" and data["batch_id"] == "fake-batch-1" and data["fake_client"] is True
    assert data["mode"] == "ci" and data["dataset_version"] == "0.0.1" and len(data["answers_sha256"]) == 64
    m = data["metrics"]
    assert m["supported"] == 2 and m["golden_answer_supported"] == round(2 / 3, 4) and m["not_judged_ids"] == ["ga-003"]
    assert data["calibration"] == {"status": "measured", "items": 3, "percent_agreement": round(2 / 3, 4),
                                   "cohens_kappa": 0.0, "graded_by": None, "human_graded": False}
    assert "MODEL-GRADED, not an independent check" in r.stdout


def test_batch_id_collects_without_submitting(tmp_path):
    r = run(str(answers_file(tmp_path)), "--dataset", str(SMALL), "--batch-id", "existing-123",
            "--calibration", str(labels_file(tmp_path, {"ga-001": None})), "--report-dir", str(tmp_path), "--fake-client")
    assert r.returncode == 0, r.stderr
    assert "collecting batch existing-123 (no new submit)" in r.stdout and "submitted" not in r.stdout
    (report,) = tmp_path.glob("judge-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["batch_id"] == "existing-123" and data["calibration"]["status"] == "pending"


def test_fake_client_refused_without_test_backends(tmp_path):
    r = run(str(answers_file(tmp_path)), "--dataset", str(SMALL), "--report-dir", str(tmp_path), "--fake-client",
            test_backends=False)
    assert r.returncode == 1 and "tests only" in r.stderr and not list(tmp_path.glob("judge-*.json"))


def test_dataset_version_mismatch_is_refused(tmp_path):
    path = answers_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["dataset_version"] = "9.9.9"
    path.write_text(json.dumps(data), encoding="utf-8")
    r = run(str(path), "--dataset", str(SMALL), "--report-dir", str(tmp_path), "--fake-client")
    assert r.returncode == 1 and "answers file is for dataset 9.9.9" in r.stderr


def test_write_payloads_then_verdicts_file(tmp_path):
    answers = answers_file(tmp_path)
    payloads = tmp_path / "payloads.jsonl"
    r = run(str(answers), "--dataset", str(SMALL), "--write-payloads", str(payloads), test_backends=False)
    assert r.returncode == 0, r.stderr
    lines = [json.loads(x) for x in payloads.read_text(encoding="utf-8").splitlines()]
    assert [x["id"] for x in lines] == ["ga-001", "ga-002", "ga-003"]
    assert "Grade only against" in payloads.with_suffix(".rubric.txt").read_text(encoding="utf-8")
    assert not list(tmp_path.glob("judge-*.json"))
    verdicts = tmp_path / "verdicts.jsonl"
    verdicts.write_text("\n".join(json.dumps({"id": x["id"], "verdict": {
        "supported": True, "reason": "ok",
        "citations": [{"citation_id": c["citation_id"], "supports_claim": True} for c in x["payload"]["answer_citations"]]}})
        for x in lines[:2]) + "\n", encoding="utf-8")
    r = run(str(answers), "--dataset", str(SMALL), "--calibration", str(labels_file(tmp_path, {"ga-001": None})),
            "--report-dir", str(tmp_path), "--verdicts-file", str(verdicts), test_backends=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reading 2 verdicts from verdicts.jsonl" in r.stdout
    (rep,) = tmp_path.glob("judge-*.json")
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["backend"] == "claude-code-subagent" and data["fake_client"] is False
    assert data["verdicts_file"] == "verdicts.jsonl" and len(data["verdicts_sha256"]) == 64
    assert data["batch_id"].startswith("file:verdicts.jsonl:")
    assert data["metrics"]["supported"] == 2 and data["metrics"]["not_judged_ids"] == ["ga-003"]
