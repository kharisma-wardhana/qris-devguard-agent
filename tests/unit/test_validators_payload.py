"""Payload validator (p4 spec AC #6, #7)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from app.mcp_server import config
from app.validators import payload as pv
from app.validators.fixtures import apply_patch

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "knowledge_base" / "fixtures" / "schemas"
PAYLOADS = ROOT / "knowledge_base" / "fixtures" / "payloads"
RULES_VERSION = config.mcp()["validators"]["rules_version"]


def _suites():
    for path in sorted(PAYLOADS.glob("*.yaml")):
        yield yaml.safe_load(path.read_text(encoding="utf-8"))


def _cases():
    for suite in _suites():
        for case in suite["cases"]:
            yield pytest.param(suite, case, id=case["id"])


def _pairs(items):
    return sorted((i["code"], i["pointer"]) for i in items)


@pytest.mark.parametrize("suite,case", list(_cases()))
def test_fixture_case(suite, case):
    data = apply_patch(suite["base"], case.get("patch"))
    res = pv.validate(data, suite["endpoint_id"], suite["spec_version"], schemas_dir=SCHEMAS,
                      rules_version=RULES_VERSION)
    exp = case["expect"]
    assert res.valid is exp["valid"]
    assert _pairs(res.errors) == _pairs(exp.get("errors", []))
    assert _pairs(res.warnings) == _pairs(exp.get("warnings", []))


def test_minimum_case_counts_per_endpoint():
    for suite in _suites():
        pos = sum(c["expect"]["valid"] for c in suite["cases"])
        neg = sum(not c["expect"]["valid"] for c in suite["cases"])
        assert pos >= 4 and neg >= 8, suite["endpoint_id"]


def test_every_business_rule_is_both_broken_and_satisfied():
    from app.validators.rules import RULES
    broken = {e["code"] for s in _suites() for c in s["cases"] for e in c["expect"].get("errors", [])}
    for code in ("RULE_TIMESTAMP_INVALID_DATE", "RULE_VALIDITY_NOT_AFTER_TIMESTAMP",
                 "RULE_ORIGINAL_REFERENCE_COUNT", "RULE_SERVICE_CODE_MISMATCH"):
        assert code in broken
    assert len(RULES) == 4                       # each rule has a breaking case above; positives satisfy all


def test_input_is_not_modified():
    suite = next(s for s in _suites() if s["endpoint_id"] == "qr-mpm-query")
    data = apply_patch(suite["base"], {"rename": {"/headers/CHANNEL-ID": "channel-id"}})
    before = hashlib.sha256(json.dumps(data, sort_keys=False).encode()).hexdigest()
    pv.validate(data, "qr-mpm-query", "0.1.0", schemas_dir=SCHEMAS, rules_version=RULES_VERSION)
    assert hashlib.sha256(json.dumps(data, sort_keys=False).encode()).hexdigest() == before
    assert "channel-id" in data["headers"]


def test_versions_reported():
    suite = next(_suites())
    res = pv.validate(suite["base"], suite["endpoint_id"], suite["spec_version"], schemas_dir=SCHEMAS,
                      rules_version=RULES_VERSION)
    assert res.schema_version == suite["spec_version"]
    assert res.rules_version == RULES_VERSION
    assert res.schema_id.endswith(suite["spec_version"])


def test_unknown_version_is_refused_not_approximated():
    with pytest.raises(pv.UnknownSchema):
        pv.validate({}, "qr-mpm-generate", "9.9.9", schemas_dir=SCHEMAS, rules_version=RULES_VERSION)


def test_errors_never_echo_values():
    suite = next(s for s in _suites() if s["endpoint_id"] == "qr-mpm-generate")
    secretish = "Bearer-NOT-REAL-but-secret-shaped-0123456789abcdef"
    data = apply_patch(suite["base"], {"set": {"/headers/Authorization": secretish,
                                               "/body/amount/value": secretish}})
    res = pv.validate(data, "qr-mpm-generate", "0.1.0", schemas_dir=SCHEMAS, rules_version=RULES_VERSION)
    assert not res.valid
    assert secretish not in json.dumps(res.as_dict())


def test_synthetic_flag():
    assert pv.load_schema(SCHEMAS, "qr-mpm-generate", "0.1.0").get("x-synthetic") is True
    res = pv.validate({}, "access-token-b2b", "1.0.2", schemas_dir=SCHEMAS, rules_version=RULES_VERSION)
    assert res.synthetic is False
