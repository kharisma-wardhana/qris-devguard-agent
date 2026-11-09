"""Hybrid search (p2 spec AC #6, #7, #9) — in-memory Qdrant."""

import shutil
from pathlib import Path

import pytest
import yaml
from qdrant_client import QdrantClient

from app.rag import ingest as ing
from app.rag import search as srch
from app.rag import sparse
from app.rag.embedding import FakeEmbedder
from app.rag.store import Store

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "rag"
REAL = {e["document_id"]: e for e in yaml.safe_load(
    (ROOT / "knowledge_base" / "manifests" / "sources.yaml").read_text(encoding="utf-8"))["sources"]}
SPEC, SOP = "synthetic-qris-mpm-service-spec", "synthetic-qris-troubleshooting-sop"
CITATION_FIELDS = {"citation_id", "document_id", "document_version", "title", "page", "section",
                   "chunk_id", "source_url", "quote_or_excerpt"}


class IdentifierBlindEmbedder(FakeEmbedder):
    """Dense stand-in that cannot see identifiers (anything with a digit or hyphen) but knows one synonym."""

    def _vector(self, text):
        words = [t for t in sparse.tokenize(text) if not any(ch.isdigit() or ch == "-" for ch in t)]
        return super()._vector(" ".join(words))


def build(tmp_path, entries, files):
    for rel, src in files.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest) if isinstance(src, Path) else dest.write_text(src, encoding="utf-8")
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump({"sources": entries}, allow_unicode=True), encoding="utf-8")
    store = Store(QdrantClient(":memory:"), "test_kb")
    embedder = IdentifierBlindEmbedder(synonyms={"ganda": "duplicate"})
    report = ing.ingest(manifest, tmp_path, store, embedder)
    assert all(d.status == "ingested" for d in report.documents), report.documents
    return store, embedder


@pytest.fixture
def synthetic(tmp_path):
    files = {REAL[d]["local_path"]: ROOT / REAL[d]["local_path"] for d in (SPEC, SOP)}
    return build(tmp_path, [REAL[SPEC], REAL[SOP]], files)


def sections(run, k=5):
    return [(r.document_id, r.section_id) for r in run.results[:k]]


def test_identifier_only_query_found_by_sparse_and_fused(synthetic):
    store, emb = synthetic
    target = (SPEC, "2.response-codes")
    assert target not in sections(srch.search(store, emb, "4094700", mode="dense")), "dense is blind here"
    assert target in sections(srch.search(store, emb, "4094700", mode="sparse"))
    assert target in sections(srch.search(store, emb, "4094700", mode="hybrid"))


def test_meaning_only_query_found_by_dense_and_fused(synthetic):
    store, emb = synthetic
    target = (SPEC, "2.response-codes")
    assert target not in sections(srch.search(store, emb, "ganda", mode="sparse")), "no shared term"
    assert target in sections(srch.search(store, emb, "ganda", mode="dense"))
    assert target in sections(srch.search(store, emb, "ganda", mode="hybrid"))


def test_every_result_carries_the_citation_contract(synthetic):
    store, emb = synthetic
    run = srch.search(store, emb, "X-TIMESTAMP format")
    assert run.results and len(run.results) <= 20
    for i, r in enumerate(run.results, start=1):
        d = r.as_dict()
        assert CITATION_FIELDS <= set(d)
        assert r.citation_id == f"CIT-{i:06d}" and r.rank == i
        assert r.untrusted is True
        assert all(d[k] for k in CITATION_FIELDS - {"page", "source_url"})
        assert "(SYNTHETIC — not an official ASPI/BI document)" in r.title
        assert r.quote_or_excerpt in run.chunk_text[r.chunk_id]


def test_query_normalisation_keeps_identifiers():
    assert srch.normalise_query("  X-SIGNATURE \n  /v1.0/qr/qr-mpm-generate\t2007300 ") == \
        "X-SIGNATURE /v1.0/qr/qr-mpm-generate 2007300"


def test_bad_mode_and_empty_query_rejected(synthetic):
    store, emb = synthetic
    with pytest.raises(ValueError):
        srch.search(store, emb, "x", mode="bm25")
    with pytest.raises(ValueError):
        srch.search(store, emb, "   ")


def test_excerpts_respect_licence_limits(tmp_path):
    long_para = " ".join(f"Kalimat referensi nomor {i} tentang tanda tangan digital." for i in range(60))
    ref_entry = {**REAL[SOP], "checksum_sha256": None, "document_id": "ref-doc", "title": "Reference doc",
                 "local_path": "knowledge_base/sources/ref-doc.md", "permission": {"basis": "reference_only"}}
    syn_entry = {**REAL[SOP], "checksum_sha256": None, "local_path": "knowledge_base/synthetic/syn.md"}
    store, emb = build(tmp_path, [ref_entry, syn_entry], {
        "knowledge_base/sources/ref-doc.md": f"# Ref\n\n## 1. Tanda tangan\n\n{long_para}\n",
        "knowledge_base/synthetic/syn.md": f"# Syn (SYNTHETIC — not an official ASPI/BI document)\n\n## 1. Tanda tangan\n\n{long_para}\n",
    })
    run = srch.search(store, emb, "tanda tangan digital", limit=20)
    by_basis = {r.permission_basis: r for r in run.results}
    assert 0 < len(by_basis["reference_only"].quote_or_excerpt) <= 300
    assert 300 < len(by_basis["team_authored"].quote_or_excerpt) <= 1000


def test_version_conflict_returns_both_versions_unmerged(tmp_path):
    base = {**REAL[SOP], "checksum_sha256": None, "document_id": "synthetic-qr-validity", "title": "QR Validity Rules (SYNTHETIC — not an official ASPI/BI document)"}
    entries = [{**base, "document_version": "0.1.0", "local_path": "knowledge_base/synthetic/v010.md"},
               {**base, "document_version": "0.2.0", "local_path": "knowledge_base/synthetic/v020.md"}]
    store, emb = build(tmp_path, entries, {
        "knowledge_base/synthetic/v010.md": FIXTURES / "synthetic-conflict-v010.md",
        "knowledge_base/synthetic/v020.md": FIXTURES / "synthetic-conflict-v020.md",
    })
    run = srch.search(store, emb, "QR validity period minutes")
    hits = [r for r in run.results if r.document_id == "synthetic-qr-validity"]
    assert {r.document_version for r in hits} == {"0.1.0", "0.2.0"}
    for r in hits:
        text = run.chunk_text[r.chunk_id]
        assert ("15 minutes" in text) != ("30 minutes" in text), "no chunk mixes both versions"
