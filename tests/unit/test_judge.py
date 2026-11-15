"""LLM judge: payload, verdict parsing, metrics, calibration (p9-final spec AC #9-#12). No network: FakeBatches only."""

import json

import pytest

from app.evaluation import judge, settings

PLANTED_SECRET = "Bearer " + "abcDEF0123456789abcDEF0123456789"


def item(iid="ga-001", behaviour="answer"):
    return {"id": iid, "question": "What is the X-TIMESTAMP format?", "expected_behaviour": behaviour,
            "expected_facts": ["ISO-8601 with milliseconds and offset."] if behaviour == "answer" else [],
            "supporting_sections": [{"document_id": "synthetic-qris-mpm-service-spec", "section_id": "1.3"}]
            if behaviour == "answer" else []}


def record(iid="ga-001", citations=("c1",), match=True, excerpt="ISO-8601 example."):
    return {"id": iid, "behaviour_match": match,
            "answer": {"summary": "It is ISO-8601.", "findings": [{"status": "confirmed", "text": "ISO-8601",
                                                                   "citation_ids": list(citations)}],
                       "validation_results": [], "next_steps": [], "limitations": []},
            "citations": [{"citation_id": c, "document_id": "d", "section": "1.3", "excerpt": excerpt}
                          for c in citations]}


# ---------------------------------------------------------------- AC #9 payload

def test_payload_redacts_and_caps():
    rec = record(excerpt="x" * 1000)
    rec["answer"]["next_steps"] = [{"text": f"Send Authorization: {PLANTED_SECRET}", "citation_ids": []}]
    rec["citations"].append({"citation_id": "c2", "document_id": "d", "section": "2",
                             "excerpt": f"Authorization: {PLANTED_SECRET} " + "y" * 500})
    p = judge.build_payload(item(), rec)
    text = json.dumps(p)
    assert "abcDEF0123456789abcDEF0123456789" not in text
    assert all(len(c["excerpt"]) <= 300 for c in p["answer_citations"])
    assert set(p) == {"question", "expected_behaviour", "expected_facts", "supporting_sections", "answer",
                      "answer_citations"}


def test_request_params_use_config_and_no_fallback():
    params = judge.request_params(item(), record())
    j = settings()["judge"]
    assert params["model"] == "claude-opus-5-5" == j["model"]
    assert params["output_config"]["effort"] == j["effort"]
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" not in params and "temperature" not in params and "thinking" not in params


# ---------------------------------------------------------------- AC #10 NOT JUDGED and metrics

def ok(supported=True, cites=(("c1", True),)):
    return {"type": "succeeded", "stop_reason": "end_turn",
            "text": json.dumps({"supported": supported, "reason": "r",
                                "citations": [{"citation_id": c, "supports_claim": v} for c, v in cites]})}


@pytest.mark.parametrize("entry,reason", [
    ({"type": "succeeded", "stop_reason": "refusal", "text": ""}, "stop_reason:refusal"),
    ({"type": "errored"}, "errored"),
    ({"type": "expired"}, "expired"),
    ({"type": "canceled"}, "canceled"),
    ({"type": "succeeded", "stop_reason": "max_tokens", "text": "{"}, "stop_reason:max_tokens"),
    ({"type": "succeeded", "stop_reason": "end_turn", "text": "not json"}, "unparseable"),
    ({"type": "succeeded", "stop_reason": "end_turn", "text": json.dumps({"supported": "yes"})}, "unparseable"),
    (None, "missing"),
])
def test_not_judged_cases(entry, reason):
    v = judge.parse_entry("ga-001", entry, ["c1"])
    assert v.judged is False and v.not_judged_reason == reason and v.supported is False


def test_metrics_on_six_items_match_hand_computation():
    recs = [record("ga-001", ("a", "b")), record("ga-002", ("c",)), record("ga-003", ("d",), match=False),
            record("ga-004", ("e", "f")), record("ga-005", ()), record("ga-006", ("g",))]
    entries = {
        "ga-001": ok(True, (("a", True), ("b", False))),       # supported; 1 of 2 citations support
        "ga-002": ok(False, (("c", True),)),                   # not supported; citation supports
        "ga-003": ok(True, (("d", True),)),                    # judge yes but behaviour mismatch -> not supported
        "ga-004": {"type": "errored"},                         # NOT JUDGED: 2 citations count as not supporting
        "ga-005": ok(True, ()),                                # supported, no citations
        "ga-006": ok(True, ()),                                # judge skipped citation g -> counted as not supporting
    }
    verdicts = {r["id"]: judge.parse_entry(r["id"], entries[r["id"]], [c["citation_id"] for c in r["citations"]])
                for r in recs}
    m = judge.metrics(recs, verdicts)
    # supported: ga-001, ga-005, ga-006 = 3 of 6
    assert m["supported"] == 3 and m["golden_answer_supported"] == 0.5
    # citations: a,b,c,d,e,f,g = 7; supporting: a, c, d = 3
    assert m["citations_judged_total"] == 7 and m["citations_supporting"] == 3
    assert m["citation_support_precision"] == round(3 / 7, 4)
    assert m["not_judged"] == 1 and m["not_judged_ids"] == ["ga-004"]


def test_reason_is_capped():
    entry = ok()
    body = json.loads(entry["text"])
    body["reason"] = "z" * 2000
    entry["text"] = json.dumps(body)
    v = judge.parse_entry("ga-001", entry, ["c1"])
    assert len(v.reason) <= settings()["judge"]["max_reason_chars"]


# ---------------------------------------------------------------- AC #11 fake batch round trip

def test_fake_batches_round_trip_and_prime_without_submit():
    reqs = [{"custom_id": "ga-001", "params": judge.request_params(item(), record())}]
    fake = judge.FakeBatches({"ga-001": {"type": "expired"}})
    bid = fake.submit(reqs)
    assert fake.ended(bid) and [e["type"] for e in fake.results(bid)] == ["expired"]
    resumed = judge.FakeBatches()
    resumed.prime(reqs)
    (e,) = list(resumed.results("existing"))
    assert resumed.submitted == [] and e["type"] == "succeeded"
    assert judge.parse_entry("ga-001", e, ["c1"]).citations == {"c1": True}


# ---------------------------------------------------------------- AC #12 calibration

def test_calibration_sample_is_fixed_and_sized():
    ids = [f"ga-{n:03d}" for n in range(1, 101)]
    a, b = judge.calibration_sample(ids), judge.calibration_sample(list(reversed(ids)))
    assert a == b and len(a) == settings()["golden"]["calibration_size"]


def test_calibration_agreement_and_kappa_match_hand_values():
    recs = [record(f"ga-00{i}") for i in range(1, 5)]
    verdicts = {"ga-001": judge.parse_entry("ga-001", ok(True), ["c1"]),
                "ga-002": judge.parse_entry("ga-002", ok(True), ["c1"]),
                "ga-003": judge.parse_entry("ga-003", ok(False), ["c1"]),
                "ga-004": judge.parse_entry("ga-004", ok(False), ["c1"])}
    labels = {"ga-001": True, "ga-002": False, "ga-003": False, "ga-004": False}
    cal = judge.calibration(labels, recs, verdicts)
    # pairs (dev, judge): (T,T) (F,T) (F,F) (F,F): agreement 3/4; p_dev .25, p_judge .5; pe = .125+.375 = .5
    assert cal == {"status": "measured", "items": 4, "percent_agreement": 0.75, "cohens_kappa": 0.5}


def test_calibration_pending_until_every_label_is_set():
    recs = [record("ga-001")]
    v = {"ga-001": judge.parse_entry("ga-001", ok(), ["c1"])}
    assert judge.calibration({"ga-001": None}, recs, v)["status"] == "pending"
    assert judge.calibration({"ga-009": True}, recs, v)["items_not_in_run"] == ["ga-009"]


def test_real_client_imports_anthropic_lazily():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(judge))
    top = {n.names[0].name for n in tree.body if isinstance(n, ast.Import)}
    assert "anthropic" not in top


def test_real_client_without_the_extra_names_the_install_command(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "anthropic", None)          # import anthropic -> ImportError
    with pytest.raises(RuntimeError, match="uv sync --extra judge"):
        judge.AnthropicBatches()


def test_request_params_are_known_to_the_installed_sdk():
    anthropic = pytest.importorskip("anthropic")
    import inspect
    accepted = set(inspect.signature(anthropic.Anthropic(api_key="test-not-used").messages.create).parameters)
    assert set(judge.request_params(item(), record())) <= accepted
