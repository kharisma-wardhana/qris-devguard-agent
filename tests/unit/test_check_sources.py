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


# --- Synthetic / storage rules (spec AC #3) --------------------------------

MARKER = "(SYNTHETIC — not an official ASPI/BI document)"

SYNTHETIC = {
    **APPROVED,
    "document_id": "synthetic-mpm-spec",
    "title": f"MPM spec {MARKER}",
    "publisher": "Capstone project team",
    "source_url": None,
    "local_path": "knowledge_base/synthetic/mpm.md",
    "permission": {"basis": "team_authored", "evidence": "Authored by K. Wardhana, 2026-11-06"},
}

REFERENCE = {
    **APPROVED,
    "permission": {"basis": "reference_only", "evidence": "No published terms found; checked 2026-11-06"},
    "local_path": "knowledge_base/sources/snap.pdf",
}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "knowledge_base" / "synthetic").mkdir(parents=True)
    (tmp_path / "knowledge_base" / "synthetic" / "mpm.md").write_text("# x", encoding="utf-8")
    return tmp_path


def errors_for(src, root):
    return check_manifest(manifest(src), SCHEMA, root=root)


def test_valid_synthetic_and_reference_sources_pass(repo):
    assert check_manifest(manifest(SYNTHETIC, REFERENCE), SCHEMA, root=repo) == []


@pytest.mark.parametrize(
    "case, src, expected",
    [
        ("a", {**SYNTHETIC, "permission": {"basis": "public_terms", "evidence": "x"}}, "requires permission.basis team_authored"),
        ("b", {**SYNTHETIC, "document_id": "mpm-spec"}, "requires document_id prefix"),
        ("c", {**SYNTHETIC, "title": "MPM spec"}, "title must contain"),
        ("d-missing", {**SYNTHETIC, "local_path": None}, "requires local_path"),
        ("d-absent", {**SYNTHETIC, "local_path": "knowledge_base/synthetic/nope.md"}, "does not exist"),
        ("e", {**SYNTHETIC, "local_path": "knowledge_base/sources/mpm.md"}, "must be committed"),
        ("f", {**REFERENCE, "local_path": "knowledge_base/synthetic/snap.pdf"}, "must be under knowledge_base/sources/"),
        ("g-url", {**REFERENCE, "source_url": None}, "requires source_url"),
        ("g-evidence", {**REFERENCE, "permission": {"basis": "reference_only", "evidence": None}}, "permission.evidence"),
        ("traversal", {**SYNTHETIC, "local_path": "knowledge_base/../secrets.md"}, "must not contain '..'"),
    ],
)
def test_synthetic_and_storage_rules(repo, case, src, expected):
    errors = errors_for(src, repo)
    assert any(expected in e for e in errors), (case, errors)


# --- effective_date formats (spec AC #4) -----------------------------------

@pytest.mark.parametrize("value", ["2024-09", "2024-09-15"])
def test_effective_date_accepted(value):
    assert check_manifest(manifest({**APPROVED, "effective_date": value}), SCHEMA) == []


@pytest.mark.parametrize("value", ["2024-9", "09-2024", "2024-09-15T00:00", "2024-13"])
def test_effective_date_rejected(value):
    errors = check_manifest(manifest({**APPROVED, "effective_date": value}), SCHEMA)
    assert any(e.startswith("schema:") and "effective_date" in e for e in errors), errors
