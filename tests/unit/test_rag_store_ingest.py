"""Store and ingestion pipeline (p2 spec AC #1, #3, #4) — in-memory Qdrant, fake embedder."""

import shutil
from pathlib import Path

import pytest
import yaml
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.rag import ingest as ing
from app.rag.embedding import FakeEmbedder
from app.rag.store import DENSE, SPARSE, Store

ROOT = Path(__file__).resolve().parents[2]
REAL = {e["document_id"]: e for e in yaml.safe_load(
    (ROOT / "knowledge_base" / "manifests" / "sources.yaml").read_text(encoding="utf-8"))["sources"]}
SPEC, SOP = "synthetic-qris-mpm-service-spec", "synthetic-qris-troubleshooting-sop"
PAYLOAD_FIELDS = {"chunk_id", "document_id", "title", "publisher", "source_url", "document_version",
                  "effective_date", "language", "permission_basis", "approval_status", "page_start",
                  "page_end", "section_id", "section_title", "checksum_sha256", "pipeline_fingerprint", "text_hash",
                  "ingested_at", "text"}


@pytest.fixture
def corpus(tmp_path):
    syn = tmp_path / "knowledge_base" / "synthetic"
    syn.mkdir(parents=True)
    for doc in (SPEC, SOP):
        shutil.copy(ROOT / REAL[doc]["local_path"], syn)
    # Checksums are cleared: these tests edit file content, which a real manifest checksum would refuse.
    entries = [{**REAL[SPEC], "checksum_sha256": None}, {**REAL[SOP], "checksum_sha256": None},
               REAL["synthetic-test-fixtures"],
               {**REAL[SOP], "checksum_sha256": None, "document_id": "synthetic-misplaced",
                "local_path": f"knowledge_base/sources/{SOP}.md"}]
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump({"sources": entries}, allow_unicode=True), encoding="utf-8")
    return tmp_path, manifest


@pytest.fixture
def store():
    return Store(QdrantClient(":memory:"), "test_kb")


def run(corpus, store):
    root, manifest = corpus
    return {d.document_id: d for d in ing.ingest(manifest, root, store, FakeEmbedder()).documents}


def test_collection_schema_is_pinned(corpus, store):
    run(corpus, store)
    params = store.client.get_collection("test_kb").config.params
    assert params.vectors[DENSE].size == 1024
    assert params.vectors[DENSE].distance == qm.Distance.COSINE
    assert params.sparse_vectors[SPARSE].modifier == qm.Modifier.IDF


def test_only_approved_well_placed_sources_are_written(corpus, store):
    docs = run(corpus, store)
    assert docs[SPEC].status == docs[SOP].status == "ingested"
    assert docs["synthetic-test-fixtures"].status == "skipped"
    assert docs["synthetic-misplaced"].status == "skipped"
    assert store.count("synthetic-test-fixtures") == store.count("synthetic-misplaced") == 0
    assert store.count() == docs[SPEC].chunks + docs[SOP].chunks > 0


def test_every_payload_has_full_metadata(corpus, store):
    run(corpus, store)
    payloads = store.payloads()
    for p in payloads:
        assert set(p) == PAYLOAD_FIELDS
        nullable = {"source_url", "page_start", "page_end"}       # team_authored Markdown
        assert all(p[k] is not None for k in PAYLOAD_FIELDS - nullable), p["chunk_id"]
        assert "(SYNTHETIC — not an official ASPI/BI document)" in p["title"]
        assert p["permission_basis"] == "team_authored"
        assert len(p["checksum_sha256"]) == len(p["text_hash"]) == 64


def test_rerun_with_unchanged_checksums_writes_nothing(corpus, store):
    first = run(corpus, store)
    before = {p["chunk_id"]: p["ingested_at"] for p in store.payloads()}
    second = run(corpus, store)
    assert second[SPEC].status == second[SOP].status == "unchanged"
    assert second[SPEC].upserted == second[SOP].upserted == 0
    assert second[SPEC].chunks == first[SPEC].chunks
    assert {p["chunk_id"]: p["ingested_at"] for p in store.payloads()} == before


def test_changed_document_is_replaced_whole_and_others_untouched(corpus, store):
    root, _ = corpus
    run(corpus, store)
    sop_before = sum(p["document_id"] == SOP for p in store.payloads())
    assert sop_before > 1
    spec_before = {p["chunk_id"]: p["text_hash"] for p in store.payloads() if p["document_id"] == SPEC}
    (root / REAL[SOP]["local_path"]).write_text(
        "# SOP (SYNTHETIC — not an official ASPI/BI document)\n\n## 1. Only section\n\nNew text.\n", encoding="utf-8")
    docs = run(corpus, store)
    assert docs[SOP].status == "ingested" and docs[SPEC].status == "unchanged"
    sop_after = [p for p in store.payloads() if p["document_id"] == SOP]
    assert len(sop_after) == 1 and "New text." in sop_after[0]["text"]   # old chunks all gone
    assert {p["chunk_id"]: p["text_hash"] for p in store.payloads() if p["document_id"] == SPEC} == spec_before


def test_synthetic_entry_without_marker_fails(corpus, store):
    root, manifest = corpus
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["sources"][0]["title"] = "QRIS MPM Service Specification"
    manifest.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    docs = run(corpus, store)
    assert docs[SPEC].status == "failed" and docs[SPEC].reason == "synthetic title marker missing"
    assert store.count(SPEC) == 0


def test_run_record_holds_no_document_text(corpus, store, tmp_path):
    root, manifest = corpus
    report = ing.ingest(manifest, root, store, FakeEmbedder())
    path = ing.write_report(report, tmp_path / "reports")
    record = path.read_text(encoding="utf-8")
    assert SPEC in record and "chunks" in record
    assert "X-SIGNATURE" not in record and "partnerReferenceNo" not in record


def test_config_change_reingests_even_with_unchanged_checksum(corpus, store, monkeypatch):
    """Regression (step 10): a chunking/embedding config change must not leave stale vectors."""
    from app.rag import config

    run(corpus, store)
    real = config.retrieval()
    changed = {**real, "embedding": {**real["embedding"], "max_seq_length": real["embedding"]["max_seq_length"] + 1}}
    monkeypatch.setattr(config, "retrieval", lambda: changed)
    docs = run(corpus, store)
    assert docs[SPEC].status == docs[SOP].status == "ingested"
    assert docs[SPEC].upserted > 0
    again = run(corpus, store)
    assert again[SPEC].status == again[SOP].status == "unchanged"


def test_real_manifest_checksums_match_committed_synthetic_files():
    """The manifest pins synthetic docs by checksum; .gitattributes keeps them LF so this holds on every OS."""
    from app.rag import manifest

    for doc in (SPEC, SOP):
        check = manifest.check_source(REAL[doc], ROOT)
        assert check.status == manifest.OK, check.reason
        assert REAL[doc]["checksum_sha256"] == check.sha256

