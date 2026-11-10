"""Canonicalization profiles and the test-signature validator (p4 spec AC #8, #9, #10)."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from app.validators import canonical, keys, signature
from app.validators.fixtures import build_candidate, signature_case

ROOT = Path(__file__).resolve().parents[2]
SIG_DIR = ROOT / "knowledge_base" / "fixtures" / "signatures"
CASES = yaml.safe_load((SIG_DIR / "cases.yaml").read_text(encoding="utf-8"))
KAT = yaml.safe_load((SIG_DIR / "kat.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def keystore(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    keys.generate(d, CASES["keys"])
    return keys.Keystore(d)


def _run(case_id, ks):
    case = signature_case(CASES, case_id)
    cand = build_candidate(case, ks)
    return case, signature.verify(**{k: case["check"][k] for k in ("algorithm", "profile", "fixture_id")},
                                  components=case["components"], candidate=cand, keystore=ks)


@pytest.mark.parametrize("kat", KAT, ids=lambda k: f"{k['profile']}:{k['components']}")
def test_known_answer_string_to_sign(kat):
    comps = CASES["components"][kat["components"]]
    sts = canonical.string_to_sign(kat["profile"], comps)
    assert hashlib.sha256(sts.encode("utf-8")).hexdigest() == kat["string_to_sign_sha256"]
    if "minified_body_sha256" in kat:
        assert canonical.body_hash(canonical.minify(comps["body"])) == kat["minified_body_sha256"]


@pytest.mark.parametrize("key,data,expected", [
    (b"\x0b" * 20, b"Hi There",
     "87aa7cdea5ef619d4ff0b4241a1d6cb02379f4e2ce4ec2787ad0b30545e17cde"
     "daa833b7d6b8a702038b274eaea3f4e4be9d914eeb61f1702e696c203a126854"),
    (b"Jefe", b"what do ya want for nothing?",
     "164b7a7bfcf819e2e395fbe73b56e0a387bd64222e831fd610270cd7ea250554"
     "9758bf75c05a994a6d034f65f8f0e6fdcaeab1a34d4a6b4b636e070a38bce737"),
], ids=["rfc4231-1", "rfc4231-2"])
def test_hmac_sha512_rfc4231(key, data, expected):
    assert hmac.new(key, data, hashlib.sha512).hexdigest() == expected


@pytest.mark.parametrize("case_id", [c["id"] for c in CASES["cases"]])
def test_signature_case(case_id, keystore):
    case, res = _run(case_id, keystore)
    assert (res.status, res.diagnostic_code) == (case["expect"]["status"], case["expect"]["diagnostic_code"])


def test_minimum_case_counts_per_profile():
    counts = Counter((c["check"]["profile"], c["expect"]["status"]) for c in CASES["cases"])
    for profile in canonical.PROFILES:
        assert counts[(profile, "VALID")] >= 3
        assert counts[(profile, "INVALID")] >= 8
        assert counts[(profile, "INCONCLUSIVE")] >= 3


def test_every_named_mistake_has_a_case():
    used = {(c["candidate"].get("profile"), c["candidate"].get("mistake")) for c in CASES["cases"]}
    for profile, mistakes in canonical.MISTAKES.items():
        for code in mistakes:
            assert (profile, code) in used, (profile, code)


def test_results_never_contain_string_to_sign_key_or_signature(keystore):
    for c in CASES["cases"]:
        case = signature_case(CASES, c["id"])
        cand = build_candidate(case, keystore)
        res = signature.verify(**{k: case["check"][k] for k in ("algorithm", "profile", "fixture_id")},
                               components=case["components"], candidate=cand, keystore=keystore)
        text = json.dumps(res.as_dict())
        assert cand not in text
        try:
            sts = canonical.string_to_sign(case["check"]["profile"], case["components"])
            assert sts not in text
        except (canonical.CanonicalError, KeyError):
            pass
        for fid in keystore.ids():
            k = keystore.get(fid)
            if k.key_type == "hmac":
                assert k.material.hex() not in text
        assert "PRIVATE KEY" not in text


def test_assumption_is_reported_for_rsa_profile(keystore):
    _, res = _run("sig-at-valid", keystore)
    assert any("PKCS#1 v1.5" in a for a in res.assumptions)
    assert res.string_to_sign_sha256 and len(res.string_to_sign_sha256) == 64


def test_inconclusive_has_no_hash(keystore):
    _, res = _run("sig-gen-missing-token", keystore)
    assert res.status == "INCONCLUSIVE" and res.string_to_sign_sha256 is None


def test_keystore_rejects_path_like_ids(keystore):
    for bad in ("../syn-rsa-01", "syn-rsa-01/../x", "C:\\x", "", "SYN-RSA-01"):
        assert keystore.get(bad) is None


def test_test_key_repr_hides_material(keystore):
    k = keystore.get("syn-hmac-01")
    assert k.material.hex() not in repr(k)


def test_generate_keeps_existing_keys(tmp_path):
    assert sorted(keys.generate(tmp_path, {"syn-hmac-01": "hmac"})) == ["syn-hmac-01"]
    before = (tmp_path / "syn-hmac-01.hmac").read_bytes()
    assert keys.generate(tmp_path, {"syn-hmac-01": "hmac", "syn-rsa-01": "rsa"}) == ["syn-rsa-01"]
    assert (tmp_path / "syn-hmac-01.hmac").read_bytes() == before
