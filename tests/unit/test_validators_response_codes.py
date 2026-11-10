"""Response-code lookup and SNAP table row parsing (p4 spec AC #11, #12)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.mcp_server import config
from app.validators import response_codes as rc
from app.validators.fixtures import response_case, response_case_matches

ROOT = Path(__file__).resolve().parents[2]
CASES = yaml.safe_load((ROOT / "knowledge_base/fixtures/response_codes/cases.yaml").read_text(encoding="utf-8"))
TABLES = config.mcp()["validators"]["response_code_tables"]


def cap(synthetic: bool) -> int:
    return 1000 if synthetic else 300


def _run(case, tables=TABLES):
    return response_case(case, tables_cfg=tables, root=ROOT, excerpt_cap=cap)


@pytest.mark.parametrize("case", [c for c in CASES["cases"] if not c.get("requires_table")], ids=lambda c: c["id"])
def test_committed_case(case):
    res = _run(case)
    assert response_case_matches(case, res), res.as_dict()


def test_every_synthetic_code_has_a_found_case():
    table = rc.load_table(ROOT / TABLES["qris-mpm"]["0.1.0"]["path"])
    codes = {e["http_status"] + e["service_code"] + e["case_code"] for e in table["entries"]}
    found = {c["code"] for c in CASES["cases"] if c["api_family"] == "qris-mpm" and c["expect"]["status"] == "FOUND"}
    assert codes == found


def test_snap_without_table_is_unavailable_never_not_found(tmp_path):
    tables = {**TABLES, "snap": {"1.0.2": {**TABLES["snap"]["1.0.2"], "path": str(tmp_path / "absent.json")}}}
    case = {"api_family": "snap", "spec_version": "1.0.2", "code": "9999999"}
    res = response_case(case, tables_cfg=tables, root=Path("/"), excerpt_cap=cap)
    assert res.status == "TABLE_UNAVAILABLE" and res.match is None and not res.suggestions


def test_not_found_carries_no_meaning_and_labels_suggestions():
    res = _run({"api_family": "qris-mpm", "spec_version": "0.1.0", "code": "4004703"})
    assert res.status == "NOT_FOUND" and res.match is None
    assert res.suggestions and all(s["label"] == "suggestion, not a match" for s in res.suggestions)


def test_synthetic_label_and_citation():
    res = _run({"api_family": "qris-mpm", "spec_version": "0.1.0", "code": "4014700"})
    assert res.synthetic is True
    assert res.citation["document_id"] == "synthetic-qris-mpm-service-spec" and res.citation["synthetic"] is True


def test_generic_service_code_match_and_cap():
    table = {"document_version": "1.0.2", "entries": [
        {"http_status": "401", "service_code": "any", "case_code": "00", "category": "System",
         "message": "M", "description": "x" * 900, "page": 565}]}
    meta = {"document_id": "snap-data-technical-spec", "section": "Part 6", "synthetic": False}
    res = rc.lookup("4017300", "snap", "1.0.2", table=table, table_meta=meta, excerpt_cap=300)
    assert res.status == "FOUND" and res.match["match_type"] == "generic_service_code"
    assert len(res.match["description"]) <= 300 and res.citation["page"] == 565


@pytest.mark.parametrize("bad", ["401730", "40173000", "40173OO", "", "../x"])
def test_malformed_code_is_rejected(bad):
    with pytest.raises(ValueError):
        rc.lookup(bad, "snap", "1.0.2", table=None, table_meta={}, excerpt_cap=300)


def test_parse_pdf_rows_merges_continuations_and_skips_headers():
    rows = [
        (565, ["Category", "", "HTTP", "", "Service\nCode", "Case\nCode", "Response\nMessage", "Description"]),
        (565, [None, None, "Status", None, None, None, None, None]),
        (565, ["System", "401", None, None, "any", "01", "Synthetic\nMessage", "First part of a"]),
        (566, ["", "", None, None, "", "", "", "split description"]),
        (566, ["Business", "403", None, None, "any", "14", "Other", "Other text"]),
        (566, ["Service"]),
        (566, ["Business", "40X", None, None, "any", "14", "Bad", "Bad"]),
    ]
    out = rc.parse_pdf_rows(rows)
    assert [(e["http_status"], e["case_code"], e["page"]) for e in out] == [("401", "01", 565), ("403", "14", 566)]
    assert out[0]["message"] == "Synthetic Message"
    assert out[0]["description"] == "First part of a split description"


def test_local_snap_cases_when_table_extracted():
    table_path = ROOT / TABLES["snap"]["1.0.2"]["path"]
    if not table_path.is_file():
        pytest.skip("SNAP table not extracted on this machine (always the case in CI)")
    for case in (c for c in CASES["cases"] if c.get("requires_table")):
        res = _run(case)
        assert response_case_matches(case, res), (case["id"], res.status)
