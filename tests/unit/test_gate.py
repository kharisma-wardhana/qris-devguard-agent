"""Release gate grading (p9-final spec AC #16-#21). Synthetic evidence from tests/fixtures/evaluation/gate_reports.py."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.evaluation import gate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures" / "evaluation"))
import gate_reports  # noqa: E402

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
REPORT_GATES = {"rag_candidate_recall_at_20", "reranked_recall_at_5", "citation_validity", "citation_support_precision",
                "validator_fixture_accuracy", "golden_answer_supported", "agent_scenarios_passed", "crash_resume",
                "hitl_approval_enforced", "langfuse_alert_fired"}
CRITICAL = {"secret_leakage", "unauthorized_side_effects", "approval_bypass", "fabricated_crypto_guidance"}


def grade(tmp_path, now=NOW, manifest=None, **overrides):
    gate_reports.write_all(tmp_path, **overrides)
    result = gate.run(tmp_path, now, manifest or tmp_path / "no-manifest.yaml")
    return result, {g["name"]: g for g in result["gates"]}


def test_all_fifteen_gates_are_graded_with_evidence_fields(tmp_path):
    result, by = grade(tmp_path)
    assert set(by) == REPORT_GATES | CRITICAL | {"reproducible_deployment"}
    for name in REPORT_GATES | CRITICAL:
        assert by[name]["status"] == "PASS", (name, by[name])
        for ev in by[name]["evidence"]:
            assert len(ev["sha256"]) == 64 and ev["date"] and ev["path"]
    assert by["agent_scenarios_passed"]["value"] == {"passed": 4, "ran": 4}
    assert by["reproducible_deployment"]["status"] == "NOT RUN"
    assert result["verdict"] == "FAIL" and result["not_passing"] == ["reproducible_deployment"]


def test_missing_report_is_not_run_and_fails_the_verdict(tmp_path):
    result, by = grade(tmp_path, judge=None)
    assert by["golden_answer_supported"]["status"] == "NOT RUN"
    assert "no evidence" in by["golden_answer_supported"]["note"]
    assert result["verdict"] == "FAIL"


def test_old_evidence_is_stale(tmp_path):
    _, by = grade(tmp_path, now=datetime(2026, 12, 3, tzinfo=timezone.utc))      # 15 days after the reports
    assert by["citation_validity"]["status"] == "STALE"
    _, by = grade(tmp_path, now=datetime(2026, 12, 2, tzinfo=timezone.utc))      # 14 days: still fresh
    assert by["citation_validity"]["status"] == "PASS"


def test_dataset_version_mismatch_is_stale(tmp_path):
    _, by = grade(tmp_path, retrieval={"dataset_version": "0.1.0"})
    assert by["reranked_recall_at_5"]["status"] == "STALE" and "0.1.0" in by["reranked_recall_at_5"]["note"]


def test_ci_or_fake_evidence_is_not_accepted_for_live_gates(tmp_path):
    _, by = grade(tmp_path, judge={"mode": "ci"})
    assert by["golden_answer_supported"]["status"] == "NOT RUN"
    assert "only evidence in mode(s) ci" in by["golden_answer_supported"]["note"]
    _, by = grade(tmp_path / "fake", judge={"fake_client": True})
    assert by["citation_support_precision"]["status"] == "NOT RUN"


def test_newest_accepted_report_wins(tmp_path):
    gate_reports.write_all(tmp_path)
    older = {"mode": "live", "dataset_version": "1.0.0", "generated_at": "2026-11-17T10:00:00+00:00",
             "metrics": {"golden_answer_supported": 0.10, "citation_support_precision": 0.10}}
    (tmp_path / "judge-2026-11-17T100000.json").write_text(json.dumps(older), encoding="utf-8")
    newer_ci = {**older, "mode": "ci", "generated_at": "2026-11-19T10:00:00+00:00"}
    (tmp_path / "judge-2026-11-19T100000.json").write_text(json.dumps(newer_ci), encoding="utf-8")
    by = {g["name"]: g for g in gate.run(tmp_path, NOW, tmp_path / "none")["gates"]}
    assert by["golden_answer_supported"]["value"] == 0.93


def test_retrieval_uses_all_fifty_queries_current_values(tmp_path):
    _, by = grade(tmp_path, retrieval={"before_after": {
        "original": {"candidate_recall@20": 1.0, "reranked_recall@5": 0.9},
        "all": {"candidate_recall@20": 0.92, "reranked_recall@5": 0.8}}})
    assert by["rag_candidate_recall_at_20"]["status"] == "PASS" and by["rag_candidate_recall_at_20"]["value"] == 0.92
    assert by["reranked_recall_at_5"]["status"] == "FAIL" and by["reranked_recall_at_5"]["value"] == 0.8


def test_agent_scenarios_need_four_of_four(tmp_path):
    flows = [{"id": f"f{n}", "passed": n != 0, "failures": ["sources_include"] if n == 0 else []} for n in range(4)]
    _, by = grade(tmp_path, **{"agent-flows-live": {"flows": flows}})
    assert by["agent_scenarios_passed"]["status"] == "FAIL" and by["agent_scenarios_passed"]["value"] == {"passed": 3, "ran": 4}


def test_critical_gate_fails_verdict_even_alone(tmp_path):
    result, by = grade(tmp_path, **{"answers-live": {"summary": {"citation_validity": 1.0, "fabricated_crypto": 1,
                                                                  "secret_findings": 0}}})
    assert by["fabricated_crypto_guidance"]["status"] == "FAIL" and by["fabricated_crypto_guidance"]["value"] == 1
    assert result["critical_not_passing"] == ["fabricated_crypto_guidance"] and result["verdict"] == "FAIL"


def test_critical_gate_sums_sources_and_reads_security_cases(tmp_path):
    sec = gate_reports.reports()[f"security-{gate_reports.STAMP}.json"]
    results = [dict(r, status="FAIL") if r["id"] == "S13" else r for r in sec["results"]]
    _, by = grade(tmp_path, security={"results": results})
    assert by["secret_leakage"]["status"] == "FAIL" and by["secret_leakage"]["value"] == 1
    assert by["unauthorized_side_effects"]["status"] == "PASS"


def test_crypto_flow_failures_count(tmp_path):
    flows = [{"id": f"f{n}", "passed": n != 0, "failures": ["no_valid_signature_claim"] if n == 0 else []}
             for n in range(4)]
    _, by = grade(tmp_path, **{"agent-flows-live": {"flows": flows}})
    assert by["fabricated_crypto_guidance"]["status"] == "FAIL"


def test_missing_security_case_is_not_run(tmp_path):
    _, by = grade(tmp_path, security={"results": [{"id": "S01", "status": "PASS"}]})
    assert by["hitl_approval_enforced"]["status"] == "NOT RUN" and by["approval_bypass"]["status"] == "NOT RUN"


def test_crash_resume_not_run_is_not_pass(tmp_path):
    _, by = grade(tmp_path, **{"crash-resume": {"status": "NOT RUN"}})
    assert by["crash_resume"]["status"] == "NOT RUN"
    _, by = grade(tmp_path / "f", **{"crash-resume": {"status": "FAIL"}})
    assert by["crash_resume"]["status"] == "FAIL"


def test_synthetic_only_alert_fails(tmp_path):
    _, by = grade(tmp_path, alerts={"real_alert_fired": False, "real": []})
    assert by["langfuse_alert_fired"]["status"] == "FAIL"


def test_latency_is_reported_not_gated(tmp_path):
    result, by = grade(tmp_path)
    assert "latency" in result["reported_not_gated"] and not any("ttft" in n for n in by)
    assert result["reported_not_gated"]["judge"]["calibration"]["status"] == "measured"


@pytest.mark.parametrize("thr,value,ok", [({"min": 0.9}, 0.9, True), ({"min": 0.9}, 0.89, False),
                                          ({"max": 0}, 0, True), ({"max": 0}, 1, False),
                                          ({"must_pass": True}, True, True), ({"must_pass": True}, False, False),
                                          ({"min": 4, "of": 4}, {"passed": 4, "ran": 4}, True),
                                          ({"min": 4, "of": 4}, {"passed": 4, "ran": 5}, False)])
def test_compare(thr, value, ok):
    assert gate.compare(value, thr) is ok


def test_markdown_block_is_replaced_in_place(tmp_path):
    result, _ = grade(tmp_path / "r")
    md = tmp_path / "release-report.md"
    md.write_text("# Release\n\nIntro kept.\n\n" + gate.BEGIN + "\nold\n" + gate.END + "\n\nOutro kept.\n", encoding="utf-8")
    gate.write_markdown(md, gate.markdown(result))
    text = md.read_text(encoding="utf-8")
    assert "Intro kept." in text and "Outro kept." in text and "\nold\n" not in text
    assert "**Verdict: FAIL**" in text and "| reranked_recall_at_5 |" in text and "Reported, not gated" in text


def test_complete_manifest_makes_every_gate_pass(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_manifest import complete

    from app.evaluation import manifest
    m = tmp_path / "manifest.yaml"
    m.write_text(manifest.dump(complete()), encoding="utf-8")
    result, by = grade(tmp_path / "r", manifest=m)
    assert by["reproducible_deployment"]["status"] == "PASS"
    assert result["verdict"] == "PASS" and result["not_passing"] == []
    incomplete = complete()
    incomplete["gpu"]["sku"] = None
    m.write_text(manifest.dump(incomplete), encoding="utf-8")
    _, by = grade(tmp_path / "r2", manifest=m)
    assert by["reproducible_deployment"]["status"] == "FAIL" and "gpu.sku is empty" in by["reproducible_deployment"]["note"]
