"""configs/evaluation.yaml carries every key the evaluation code reads (p9-final plan step 1)."""

from app.evaluation import settings, thresholds


def test_top_level_keys():
    s = settings()
    for key in ("reports_dir", "freshness_days", "golden", "judge", "bench", "gates", "critical"):
        assert key in s, key
    assert s["freshness_days"] == 14


def test_judge_settings():
    j = settings()["judge"]
    assert j["model"] == "claude-opus-5-5"
    assert {"effort", "prompt_version", "max_tokens", "max_reason_chars", "poll_seconds"} <= set(j)


def test_golden_excerpt_cap_matches_public_quote_cap():
    assert settings()["golden"]["max_excerpt_chars"] == 300


def test_every_threshold_gate_has_an_evidence_mapping():
    s, t = settings(), thresholds()
    assert set(s["gates"]) == set(t["gates"])
    assert set(s["critical"]) == set(t["critical"])
    for name, g in s["gates"].items():
        assert "value" in g and "modes" in g, name
        assert "glob" in g or "manifest" in g, name
    for name, c in s["critical"].items():
        assert c["sources"], name
        for src in c["sources"]:
            assert {"glob", "value", "modes"} <= set(src), name


def test_live_only_gates_reject_ci():
    gates = settings()["gates"]
    for name in ("golden_answer_supported", "citation_support_precision", "citation_validity",
                 "agent_scenarios_passed", "langfuse_alert_fired"):
        assert gates[name]["modes"] == ["live"], name


def test_retrieval_gates_use_all_queries():
    gates = settings()["gates"]
    assert gates["rag_candidate_recall_at_20"]["value"].startswith("before_after.all.")
    assert gates["reranked_recall_at_5"]["value"].startswith("before_after.all.")
