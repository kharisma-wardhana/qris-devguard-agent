"""p9 ui-refresh: the release-gate reader behind GET /api/v1/evaluations/release-gate (docs/p9-ui-refresh/spec.md
Behaviour §8; AC #2, #3, #5)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.agent import config
from app.api import reports

ROOT = Path(__file__).resolve().parents[2]
HOSTILE = "[click](https://evil.example/x) <b>bold</b>"
FORBIDDEN_KEYS = ("path", "sha256", "freshness_days", "reported_not_gated")


def gate(name="g1", status="PASS", dates=("2026-11-09T07:33:07+00:00",), **extra):
    return {"name": name, "kind": "gate", "status": status, "value": 0.92, "threshold": {"min": 0.9},
            "evidence": [{"path": f"evals/reports/{name}.json", "sha256": "ab" * 32, "date": d} for d in dates],
            "note": "", **extra}


def report(generated_at="2026-11-15T06:55:20+00:00", gates=None, **extra):
    return {"generated_at": generated_at, "verdict": "FAIL", "not_passing": ["g2"], "critical_not_passing": [],
            "freshness_days": 14, "gates": gates if gates is not None else [gate(), gate("g2", "FAIL")],
            "reported_not_gated": {"latency": {"evidence": "bench.json"}}, **extra}


def write(directory: Path, name: str, data) -> None:
    (directory / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def keys_anywhere(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_anywhere(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_anywhere(v)}
    return set()


def test_no_report_is_missing(tmp_path):
    with pytest.raises(reports.ReportError) as err:
        reports.release_gate(tmp_path)
    assert err.value.missing


def test_only_unparsable_or_non_object_reports_are_missing(tmp_path):
    write(tmp_path, "release-gate-2026-11-15T000000.json", "{not json")
    write(tmp_path, "release-gate-2026-11-15T000001.json", [1, 2])
    with pytest.raises(reports.ReportError) as err:
        reports.release_gate(tmp_path)
    assert err.value.missing


def test_other_report_kinds_are_ignored(tmp_path):
    write(tmp_path, "retrieval-2026-11-15T000000.json", report())
    with pytest.raises(reports.ReportError):
        reports.release_gate(tmp_path)


def test_newest_generated_at_wins_then_file_name(tmp_path):
    write(tmp_path, "release-gate-a.json", report("2026-11-15T09:00:00+00:00", [gate("newest")]))
    write(tmp_path, "release-gate-z.json", report("2026-11-14T09:00:00+00:00", [gate("older")]))
    assert [g["name"] for g in reports.release_gate(tmp_path)["gates"]] == ["newest"]
    write(tmp_path, "release-gate-b.json", report("2026-11-15T09:00:00+00:00", [gate("tie-later-name")]))
    assert [g["name"] for g in reports.release_gate(tmp_path)["gates"]] == ["tie-later-name"]


@pytest.mark.parametrize("drop", ["gates", "verdict", "generated_at"])
def test_a_report_missing_a_required_field_is_unusable(tmp_path, drop):
    data = report()
    del data[drop]
    write(tmp_path, "release-gate-x.json", data)
    with pytest.raises(reports.ReportError) as err:
        reports.release_gate(tmp_path)
    assert not err.value.missing and "release-gate" not in str(err.value)


def test_a_malformed_gate_is_unusable(tmp_path):
    write(tmp_path, "release-gate-x.json", report(gates=[{"status": "PASS"}]))
    with pytest.raises(reports.ReportError) as err:
        reports.release_gate(tmp_path)
    assert not err.value.missing


def test_response_is_an_allowlist_without_paths_or_hashes(tmp_path):
    write(tmp_path, "release-gate-x.json", report())
    out = reports.release_gate(tmp_path)
    assert set(out) == {"generated_at", "verdict", "not_passing", "critical_not_passing", "gates"}
    for g in out["gates"]:
        assert set(g) == {"name", "kind", "status", "value", "threshold", "evidence_date", "note"}
    assert not keys_anywhere(out) & set(FORBIDDEN_KEYS)
    text = json.dumps(out)
    assert "evals/reports" not in text and "ab" * 32 not in text and "bench.json" not in text


def test_evidence_date_is_the_newest_and_values_pass_through(tmp_path):
    g = gate(dates=("2026-11-09T00:00:00+00:00", "2026-11-15T00:00:00+00:00"), name=HOSTILE)
    g["value"] = {"passed": 3, "of": 4}
    write(tmp_path, "release-gate-x.json", report(gates=[g, gate("none", dates=())]))
    first, second = reports.release_gate(tmp_path)["gates"]
    assert first["evidence_date"] == "2026-11-15T00:00:00+00:00" and second["evidence_date"] is None
    assert first["name"] == HOSTILE and first["value"] == {"passed": 3, "of": 4}


def test_local_reports_give_the_measured_fail_verdict():
    """evals/reports/ is git-ignored, so CI has no report; this runs where the developer's evidence exists."""
    directory = ROOT / config.agent()["api"]["reports_dir"]
    if not any(directory.glob(config.agent()["api"]["release_gate_glob"])):
        pytest.skip("no local release-gate report (evals/reports/ is git-ignored)")
    out = reports.release_gate()
    assert out["verdict"] == "FAIL" and len(out["gates"]) == 15


def test_api_reports_dir_matches_the_evaluation_config():
    """The API reads configs/agent.yaml only; this pins it to the release gate's own reports_dir."""
    evaluation = yaml.safe_load((ROOT / "configs" / "evaluation.yaml").read_text(encoding="utf-8"))
    assert config.agent()["api"]["reports_dir"] == evaluation["reports_dir"]


def test_the_api_does_not_import_app_evaluation():
    code = ("import sys; import app.api.app, app.api.reports; "
            "bad = sorted(m for m in sys.modules if m == 'app.evaluation' or m.startswith('app.evaluation.')); "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# --- p9 ui-refresh step 2: sources (spec AC #1) ---------------------------------------------------------------

from app.rag import manifest as rag_manifest  # noqa: E402

MANIFEST = ROOT / "knowledge_base" / "manifests" / "sources.yaml"
SOURCE_KEYS = {"document_id", "title", "publisher", "kind", "document_version", "effective_date", "synthetic"}


def test_sources_are_exactly_the_retrievable_documents_in_manifest_order():
    entries = rag_manifest.load_manifest(MANIFEST)
    keep = rag_manifest.retrievable_document_ids(entries)
    out = reports.sources()
    assert [s["document_id"] for s in out] == [e["document_id"] for e in entries if e["document_id"] in keep]
    assert out and all(set(s) == SOURCE_KEYS for s in out)


def test_sources_never_carry_paths_checksums_permission_or_urls():
    text = json.dumps(reports.sources())
    for entry in rag_manifest.load_manifest(MANIFEST):
        for key in ("local_path", "checksum_sha256", "source_url"):
            if entry.get(key):
                assert str(entry[key]) not in text, key
        if (entry.get("permission") or {}).get("evidence"):
            assert entry["permission"]["evidence"][:40] not in text


def test_synthetic_is_true_exactly_for_team_authored_entries():
    basis = {e["document_id"]: (e.get("permission") or {}).get("basis")
             for e in rag_manifest.load_manifest(MANIFEST)}
    for s in reports.sources():
        assert s["synthetic"] is (basis[s["document_id"]] == "team_authored")


def test_sources_from_a_temp_manifest_filter_and_stringify(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": [
        {"document_id": "a", "title": HOSTILE, "kind": "specification", "approval_status": "approved",
         "document_version": "1.0.2", "effective_date": "2024-09", "local_path": "x/secret.pdf",
         "permission": {"basis": "reference_only", "evidence": "checked"}},
        {"document_id": "synthetic-b", "title": "B", "kind": "sop", "approval_status": "approved",
         "effective_date": __import__("datetime").date(2026, 11, 6), "permission": {"basis": "team_authored"}},
        {"document_id": "c", "title": "C", "kind": "specification", "approval_status": "candidate"},
        {"document_id": "f", "title": "F", "kind": "fixture_set", "approval_status": "approved"},
    ]}), encoding="utf-8")
    out = reports.sources(path)
    assert [s["document_id"] for s in out] == ["a", "synthetic-b"]
    assert out[0]["title"] == HOSTILE and out[0]["publisher"] is None and out[0]["synthetic"] is False
    assert out[1]["effective_date"] == "2026-11-06" and out[1]["synthetic"] is True
    assert "secret.pdf" not in json.dumps(out) and "checked" not in json.dumps(out)


@pytest.mark.parametrize("content", [None, "sources: [1, 2]", "{bad yaml: ["])
def test_an_unreadable_manifest_is_unusable(tmp_path, content):
    path = tmp_path / "sources.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(reports.ReportError) as err:
        reports.sources(path)
    assert not err.value.missing and str(tmp_path) not in str(err.value)
