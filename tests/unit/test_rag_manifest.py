"""Manifest selection and file checks (p2 spec AC #1)."""

import hashlib
from pathlib import Path

import pytest

from app.rag import manifest as m

ROOT = Path(__file__).resolve().parents[2]
BODY = b"# Title\n\nBody\n"


def entry(**over):
    base = {
        "document_id": "synthetic-doc",
        "approval_status": "approved",
        "local_path": "knowledge_base/synthetic/doc.md",
        "permission": {"basis": "team_authored"},
        "checksum_sha256": None,
    }
    base.update(over)
    return base


@pytest.fixture
def root(tmp_path):
    (tmp_path / "knowledge_base" / "synthetic").mkdir(parents=True)
    (tmp_path / "knowledge_base" / "sources").mkdir(parents=True)
    (tmp_path / "knowledge_base" / "synthetic" / "doc.md").write_bytes(BODY)
    return tmp_path


def test_approved_file_in_right_area_is_ok(root):
    r = m.check_source(entry(), root)
    assert r.status == m.OK
    assert r.sha256 == hashlib.sha256(BODY).hexdigest()


@pytest.mark.parametrize("status", ["candidate", "rejected", None])
def test_not_approved_is_skipped(root, status):
    r = m.check_source(entry(approval_status=status), root)
    assert r.status == m.SKIPPED and r.reason.startswith("not approved")


def test_missing_local_path_is_skipped(root):
    r = m.check_source(entry(local_path=None), root)
    assert (r.status, r.reason) == (m.SKIPPED, "no local_path")


@pytest.mark.parametrize("path, basis", [
    ("knowledge_base/sources/doc.md", "team_authored"),          # synthetic doc in the licensed area
    ("knowledge_base/synthetic/doc.md", "reference_only"),       # reference doc in the committed area
    ("knowledge_base/synthetic/../sources/doc.md", "team_authored"),
    ("docs/doc.md", "team_authored"),
    ("knowledge_base/synthetic/doc.md", "licensed"),
])
def test_wrong_storage_area_is_skipped(root, path, basis):
    r = m.check_source(entry(local_path=path, permission={"basis": basis}), root)
    assert r.status == m.SKIPPED and r.reason.startswith("wrong storage area")


def test_disallowed_type_is_skipped(root):
    (root / "knowledge_base" / "synthetic" / "doc.txt").write_text("x", encoding="utf-8")
    r = m.check_source(entry(local_path="knowledge_base/synthetic/doc.txt"), root)
    assert r.status == m.SKIPPED and "type not allowed" in r.reason


def test_content_not_matching_extension_is_skipped(root):
    (root / "knowledge_base" / "sources" / "fake.pdf").write_text("not a pdf", encoding="utf-8")
    r = m.check_source(entry(local_path="knowledge_base/sources/fake.pdf",
                             permission={"basis": "reference_only"}), root)
    assert r.status == m.SKIPPED and "content does not match" in r.reason


def test_oversize_file_is_skipped(root, monkeypatch):
    real = m.config.retrieval()
    cfg = {**real, "ingest": {**real["ingest"], "max_file_mb": 0}}
    monkeypatch.setattr(m.config, "retrieval", lambda: cfg)
    r = m.check_source(entry(), root)
    assert r.status == m.SKIPPED and "larger than" in r.reason


def test_missing_file_fails(root):
    r = m.check_source(entry(local_path="knowledge_base/synthetic/absent.md"), root)
    assert r.status == m.FAILED and "file not found" in r.reason


def test_checksum_mismatch_fails(root):
    r = m.check_source(entry(checksum_sha256="0" * 64), root)
    assert (r.status, r.reason) == (m.FAILED, "checksum mismatch with manifest")


def test_matching_checksum_is_ok_case_insensitive(root):
    digest = hashlib.sha256(BODY).hexdigest().upper()
    assert m.check_source(entry(checksum_sha256=digest), root).status == m.OK


def test_real_manifest_selects_only_approved_entries():
    results = m.select_sources(ROOT / "knowledge_base" / "manifests" / "sources.yaml", ROOT)
    by_id = {r.document_id: r for r in results}
    assert by_id["synthetic-test-fixtures"].status == m.SKIPPED      # candidate
    assert by_id["synthetic-qris-mpm-service-spec"].status == m.OK
    assert by_id["synthetic-qris-troubleshooting-sop"].status == m.OK
