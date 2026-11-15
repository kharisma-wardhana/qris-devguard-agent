"""vllm bench serve parser (p9-final spec AC #14). Fixtures are synthetic, shaped like vLLM 0.32.0 output."""

import json
from pathlib import Path

import pytest

from app.evaluation import bench

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "evaluation" / "bench"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_takes_median_as_p50_and_p95_keys():
    row = bench.parse_result(load("bench-c1.json"))
    assert row["ttft_ms"] == {"mean": 210.5, "p50": 198.2, "p95": 262.9}
    assert row["tpot_ms"] == {"mean": 30.9, "p50": 30.7, "p95": 32.4}
    assert row["max_concurrency"] == 1 and row["completed"] == 100 and row["notes"] == []
    assert row["gated_baseline"] is True
    assert row["against_targets"]["ttft_p95_ms"] == {"target_max": 5000, "met": True}
    assert row["against_targets"]["tpot_p95_ms"] == {"target_max": 120, "met": True}


def test_concurrency_four_is_informational():
    row = bench.parse_result(load("bench-c4.json"))
    assert row["gated_baseline"] is False and "against_targets" not in row


@pytest.mark.parametrize("key", ["p95_ttft_ms", "median_tpot_ms", "completed"])
def test_missing_field_fails_clearly(key):
    data = load("bench-c1.json")
    del data[key]
    with pytest.raises(bench.BenchError, match=key):
        bench.parse_result(data)


def test_unplanned_settings_and_failures_are_noted():
    data = {**load("bench-c1.json"), "max_concurrency": 2, "num_prompts": 10, "failed": 3}
    notes = bench.parse_result(data)["notes"]
    assert len(notes) == 3


def test_missed_target_is_reported_not_hidden():
    data = {**load("bench-c1.json"), "p95_tpot_ms": 150.0}
    assert bench.parse_result(data)["against_targets"]["tpot_p95_ms"]["met"] is False


def test_report_carries_hardware_model_and_a5000_note():
    rows = [bench.parse_result(load("bench-c4.json")), bench.parse_result(load("bench-c1.json"))]
    hc = [{"all_pass": False, "ttft_ms": 1.0}, {"all_pass": True, "ttft_ms": 598.2, "ts": "t", "gpu": "x"}]
    rep = bench.build_report(rows, {"gpu_sku": "NVIDIA RTX A5000", "driver": "595.91.07", "cuda": "13.0"}, hc)
    assert [r["max_concurrency"] for r in rep["rows"]] == [1, 4]
    assert rep["model"]["revision"] and rep["model"]["quantization"] == "fp8" and rep["model"]["max_model_len"]
    assert rep["tunnel_ttft"]["ttft_ms"] == 598.2 and rep["gated"] is False
    assert any("fallback kernel" in n for n in rep["notes"])
    with pytest.raises(bench.BenchError, match="driver"):
        bench.build_report(rows, {"gpu_sku": "RTX PRO 4000", "driver": "", "cuda": "13.0"}, [])


def test_full_nvidia_smi_name_matches_the_planned_sku():
    rows = [bench.parse_result(load("bench-c1.json"))]
    hw = {"gpu_sku": "NVIDIA RTX PRO 4000 Blackwell", "driver": "595.91.07", "cuda": "13.2"}
    assert bench.build_report(rows, hw, [])["notes"] == []
    other = bench.build_report(rows, {**hw, "gpu_sku": "NVIDIA RTX A6000"}, [])["notes"]
    assert any("differs from the planned" in n for n in other)
