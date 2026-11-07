"""Embedders (p2 plan step 5): deterministic fake; clear error when the embed extra is missing."""

import math
import sys

import pytest

from app.rag import embedding


def test_fake_embedder_is_deterministic_normalised_1024():
    emb = embedding.FakeEmbedder()
    a = emb.embed_query("X-SIGNATURE gagal diverifikasi")
    b = emb.embed_documents(["X-SIGNATURE gagal diverifikasi"])[0]
    assert a == b
    assert len(a) == 1024 == emb.dimension
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    assert emb.count_tokens("anything") is None


def test_fake_embedder_synonyms_make_meaning_only_matches():
    emb = embedding.FakeEmbedder(synonyms={"kedaluwarsa": "expired"})
    q = emb.embed_query("kedaluwarsa")
    d = emb.embed_documents(["expired"])[0]
    assert sum(x * y for x, y in zip(q, d)) == pytest.approx(1.0)


def test_bge_m3_reports_missing_embed_stack(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # import now raises ImportError
    with pytest.raises(embedding.EmbedStackMissing, match="uv sync --extra embed"):
        embedding.BgeM3Embedder()
