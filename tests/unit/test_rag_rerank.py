"""Reranking (p3 spec AC #1-#3) — fake reranker, crafted search runs."""

import sys

import pytest

from app.rag import config
from app.rag import rerank as rr
from app.rag.embedding import EmbedStackMissing
from app.rag.search import Citation, SearchRun

CITATION_FIELDS = ("citation_id", "document_id", "document_version", "title", "page", "section",
                   "chunk_id", "source_url", "quote_or_excerpt", "section_id", "page_end",
                   "permission_basis", "approval_status", "untrusted", "score")


def run_with(texts: list[str], query: str = "x-signature hmac_sha512 minify body") -> SearchRun:
    results, chunk_text = [], {}
    for i, text in enumerate(texts, start=1):
        cid = f"chunk-{i}"
        chunk_text[cid] = text
        results.append(Citation(
            citation_id=f"CIT-{i:06d}", document_id="synthetic-doc", document_version="0.1.0",
            title="Doc (SYNTHETIC — not an official ASPI/BI document)", page=None, section=f"{i} S{i}",
            chunk_id=cid, source_url=None, quote_or_excerpt=text[:40], rank=i, score=1.0 / i,
            section_id=str(i), page_end=None, permission_basis="team_authored", approval_status="approved"))
    return SearchRun("run-1", query, "hybrid", results, chunk_text)


POOL = ["unrelated text about balance inquiry",
        "timestamp format with offset",
        "X-SIGNATURE uses HMAC_SHA512 over a minify body hash",
        "the body is minify before hashing",
        "nothing relevant here at all"]


def test_rerank_only_reorders_and_keeps_every_field():
    run = run_with(POOL)
    out, stats = rr.rerank(run, rr.FakeReranker())
    assert sorted(r.chunk_id for r in out.results) == sorted(r.chunk_id for r in run.results)
    assert len(out.results) == len(run.results) == stats.pairs
    before = {r.chunk_id: r for r in run.results}
    for r in out.results:
        for field in CITATION_FIELDS:
            assert getattr(r, field) == getattr(before[r.chunk_id], field), field
    scores = [r.rerank_score for r in out.results]
    assert scores == sorted(scores, reverse=True)
    assert [r.rank for r in out.results] == list(range(1, len(POOL) + 1))
    assert out.chunk_text == run.chunk_text


def test_fake_reranker_changes_the_fused_order_deterministically():
    run = run_with(POOL)
    a, _ = rr.rerank(run, rr.FakeReranker())
    b, _ = rr.rerank(run, rr.FakeReranker())
    assert [r.chunk_id for r in a.results] == [r.chunk_id for r in b.results]
    assert a.results[0].chunk_id == "chunk-3"                      # was rank 3 in the fused order
    assert [r.chunk_id for r in a.results] != [r.chunk_id for r in run.results]


def test_ties_keep_the_fused_order():
    run = run_with(["same words", "same words", "same words"], query="nothing matches")
    out, _ = rr.rerank(run, rr.FakeReranker())
    assert [r.chunk_id for r in out.results] == ["chunk-1", "chunk-2", "chunk-3"]


def test_rerank_rejects_a_wrong_number_of_scores():
    class Broken:
        def score(self, query, texts):
            return [1.0]

        def count_tokens(self, query, text):
            return None

    with pytest.raises(RuntimeError, match="1 scores for 5 candidates"):
        rr.rerank(run_with(POOL), Broken())


def test_truncated_pairs_counted_when_tokenizer_known():
    class Counting(rr.FakeReranker):
        def count_tokens(self, query, text):
            return 2000 if "minify" in text else 10

    _, stats = rr.rerank(run_with(POOL), Counting())
    assert stats.truncated_pairs == 2
    _, fake_stats = rr.rerank(run_with(POOL), rr.FakeReranker())
    assert fake_stats.truncated_pairs is None


def test_select_takes_top_n_from_config():
    out, _ = rr.rerank(run_with(POOL * 2), rr.FakeReranker())
    assert config.retrieval()["rerank"]["top_n"] == 6
    assert [r.rank for r in rr.select(out)] == [1, 2, 3, 4, 5, 6]
    assert len(rr.select(out, top_n=3)) == 3
    assert len(rr.select(run_with(POOL[:2]))) == 2                 # min(top_n, n)


def test_rerank_config_is_pinned_and_sane():
    cfg = config.retrieval()["rerank"]
    assert cfg["model_id"] == "BAAI/bge-reranker-v2-m3"
    assert len(cfg["revision"]) == 40
    assert cfg["candidates"] == config.retrieval()["search"]["limit"] == 20
    assert 5 <= cfg["top_n"] <= 8
    assert cfg["max_length"] >= 1275 + 32                          # P2's longest chunk plus a query


def test_cross_encoder_reports_missing_embed_stack(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(EmbedStackMissing, match="uv sync --extra embed"):
        rr.CrossEncoderReranker()


def test_cross_encoder_reports_uncached_model(monkeypatch):
    import types

    class Missing:
        def __init__(self, *a, **k):
            raise OSError("not found in cache")

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=Missing))
    with pytest.raises(EmbedStackMissing, match="fetch_models.py"):
        rr.CrossEncoderReranker()
