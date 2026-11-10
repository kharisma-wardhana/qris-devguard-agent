"""Fixture runner (p4 spec AC #14)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from app.mcp_server import config
from app.validators import keys
from app.validators.fixtures import apply_patch, run_all

ROOT = Path(__file__).resolve().parents[2]
V = config.mcp()["validators"]
INDEX = config.repo_path(V["fixtures_index"])


def _run(index, keys_dir, tables=None):
    wanted = yaml.safe_load((index.parent / "signatures" / "cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)
    return run_all(index, schemas_dir=config.repo_path(V["schemas_dir"]), keystore=keys.Keystore(keys_dir),
                   tables_cfg=tables or V["response_code_tables"], root=ROOT,
                   excerpt_cap=lambda s: 1000 if s else 300, rules_version=V["rules_version"])


def test_committed_fixture_set_is_100_percent(tmp_path):
    report = _run(INDEX, tmp_path)
    for name, s in report["validators"].items():
        assert s["failed"] == 0 and s["accuracy"] == 1.0, (name, [c for c in report["cases"] if c["result"] == "failed"])
    assert report["validators"]["payload"]["total"] >= 36
    assert report["validators"]["signature"]["total"] >= 28


def test_wrong_expectation_is_reported_as_failure(tmp_path):
    fx = tmp_path / "fixtures"
    shutil.copytree(INDEX.parent, fx, ignore=shutil.ignore_patterns("keys"))
    p = fx / "payloads" / "qr-mpm-generate.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["cases"][0]["expect"] = {"valid": False}
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    report = _run(fx / "fixtures.yaml", tmp_path / "keys")
    assert report["validators"]["payload"]["failed"] == 1
    assert [c["id"] for c in report["cases"] if c["result"] == "failed"] == [doc["cases"][0]["id"]]


def test_missing_snap_table_is_skipped_not_passed(tmp_path):
    tables = {**V["response_code_tables"],
              "snap": {"1.0.2": {**V["response_code_tables"]["snap"]["1.0.2"], "path": str(tmp_path / "absent.json")}}}
    report = _run(INDEX, tmp_path / "keys", tables)
    skipped = [c for c in report["cases"] if c["result"] == "skipped"]
    assert skipped and all(c["validator"] == "response_codes" and c["actual"] is None for c in skipped)
    assert report["validators"]["response_codes"]["skipped"] == len(skipped)


def test_report_holds_no_payload_values_or_signatures(tmp_path):
    import json
    text = json.dumps(_run(INDEX, tmp_path))
    assert "SYN-PLACEHOLDER-TOKEN" not in text and "SYN-MERCHANT-01" not in text


def test_apply_patch_does_not_touch_base():
    base = {"a": {"b": 1}, "c": [1]}
    out = apply_patch(base, {"set": {"/z/b": 2}, "remove": ["/c"], "rename": {"/a": "z"}})   # remove, rename, set
    assert base == {"a": {"b": 1}, "c": [1]} and out == {"z": {"b": 2}}


def test_apply_patch_rejects_bad_pointer():
    with pytest.raises(ValueError):
        apply_patch({"a": 1}, {"set": {"a": 2}})
