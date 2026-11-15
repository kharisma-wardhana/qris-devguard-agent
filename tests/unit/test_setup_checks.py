"""Helpers behind scripts/setup.sh (p9-final spec AC #3; plan step 9). No network, no real services."""

import hashlib
import socket
import time
from pathlib import Path

import yaml

from app.evaluation import setup_checks

PDF_BYTES = b"%PDF-1.7 synthetic test file\n"


def make_root(tmp_path: Path, with_pdf=True, checksum=None):
    (tmp_path / "knowledge_base" / "manifests").mkdir(parents=True)
    (tmp_path / "knowledge_base" / "sources").mkdir(parents=True)
    pdf = tmp_path / "knowledge_base" / "sources" / "snap-test-1.0.2.pdf"
    if with_pdf:
        pdf.write_bytes(PDF_BYTES)
    manifest = {"sources": [
        {"document_id": "snap-test", "version": "1.0.2", "approval_status": "approved", "kind": "specification",
         "permission": {"basis": "reference_only"}, "source_url": "https://example.org/docs/snap-test",
         "local_path": "knowledge_base/sources/snap-test-1.0.2.pdf",
         "checksum_sha256": checksum or hashlib.sha256(PDF_BYTES).hexdigest()},
        {"document_id": "synthetic-doc", "version": "0.1.0", "approval_status": "approved", "kind": "internal_sop",
         "permission": {"basis": "team_authored"}, "source_url": None,
         "local_path": "knowledge_base/synthetic/synthetic-doc.md", "checksum_sha256": "0" * 64},
        {"document_id": "unapproved-ref", "approval_status": "candidate", "permission": {"basis": "reference_only"},
         "local_path": "knowledge_base/sources/other.pdf"},
    ]}
    (tmp_path / "knowledge_base" / "manifests" / "sources.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return tmp_path


def test_present_and_matching_pdf_has_no_problems(tmp_path):
    assert setup_checks.reference_pdfs(make_root(tmp_path)) == []


def test_missing_pdf_gives_portal_page_name_and_path(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "create_connection", no_network)
    (line,) = setup_checks.reference_pdfs(make_root(tmp_path, with_pdf=False))
    assert line.startswith("MISSING snap-test:") and "https://example.org/docs/snap-test" in line
    assert "'snap-test-1.0.2.pdf'" in line and "knowledge_base/sources/snap-test-1.0.2.pdf" in line


def test_checksum_mismatch_names_the_entry(tmp_path):
    (line,) = setup_checks.reference_pdfs(make_root(tmp_path, checksum="f" * 64))
    assert line.startswith("CHECKSUM snap-test:") and "do not edit the manifest" in line


def test_synthetic_manifest_keeps_team_authored_only(tmp_path):
    root = make_root(tmp_path)
    out = tmp_path / "var" / "syn.yaml"
    assert setup_checks.synthetic_manifest(root, out) == 1
    kept = yaml.safe_load(out.read_text(encoding="utf-8"))["sources"]
    assert [e["document_id"] for e in kept] == ["synthetic-doc"]


def test_wait_returns_ports_that_never_open_within_the_timeout():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]            # bound, never listening, released at exit
    started = time.monotonic()
    assert setup_checks.wait_for([closed], timeout=0) == [closed]
    assert time.monotonic() - started < 10


def test_wait_succeeds_on_a_listening_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert setup_checks.wait_for([s.getsockname()[1]], timeout=5) == []


def test_compose_ports_reads_the_real_compose_file():
    ports = setup_checks.compose_ports(setup_checks.ROOT)
    assert 5432 in ports and 6333 in ports and all(isinstance(p, int) for p in ports)
