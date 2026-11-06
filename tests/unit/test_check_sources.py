import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_sources import DEFAULT_MANIFEST, SCHEMA_PATH, check_manifest  # noqa: E402

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

APPROVED = {
    "document_id": "snap-spec",
    "title": "SNAP spec",
    "publisher": "ASPI",
    "kind": "specification",
    "source_url": "https://apidevportal.aspi-indonesia.or.id/doc",
    "document_version": "1.0",
    "effective_date": "2026-01-01",
    "language": ["id"],
    "data_owner": "kb_maintainer",
    "permission": {"basis": "public_terms", "evidence": "Portal terms reviewed 2026-11-06"},
    "approval_status": "approved",
    "checksum_sha256": None,
}


def manifest(*sources):
    return {"manifest_version": "0.1.0", "sources": [copy.deepcopy(s) for s in sources]}


def test_committed_manifest_is_valid():
    data = yaml.safe_load(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    assert check_manifest(data, SCHEMA) == []


def test_complete_approved_source_passes():
    assert check_manifest(manifest(APPROVED), SCHEMA) == []


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("document_version", None, "requires document_version"),
        ("effective_date", None, "requires effective_date"),
        ("source_url", None, "requires source_url"),
        ("permission", {"basis": "unconfirmed", "evidence": "x"}, "confirmed permission.basis"),
        ("permission", {"basis": "public_terms", "evidence": None}, "permission.evidence"),
    ],
)
def test_approved_source_requires_provenance(field, value, expected):
    src = {**APPROVED, field: value}
    errors = check_manifest(manifest(src), SCHEMA)
    assert any(expected in e for e in errors), errors


def test_placeholder_url_rejected_even_for_candidates():
    src = {**APPROVED, "approval_status": "candidate", "source_url": "https://example.org/approved-source"}
    errors = check_manifest(manifest(src), SCHEMA)
    assert any("placeholder" in e for e in errors), errors


def test_duplicate_document_id_rejected():
    errors = check_manifest(manifest(APPROVED, APPROVED), SCHEMA)
    assert any("duplicate" in e for e in errors), errors


def test_superseded_must_point_to_known_document():
    old = {**APPROVED, "document_id": "snap-spec-old", "approval_status": "superseded", "superseded_by": "missing"}
    errors = check_manifest(manifest(APPROVED, old), SCHEMA)
    assert any("not in the manifest" in e for e in errors), errors


def test_unknown_field_rejected_by_schema():
    src = {**APPROVED, "private_key": "nope"}
    errors = check_manifest(manifest(src), SCHEMA)
    assert any(e.startswith("schema:") for e in errors), errors
