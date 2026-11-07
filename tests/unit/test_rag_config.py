"""configs/retrieval.yaml and citation limits (p2 spec AC #7, #15)."""

import pytest

from app.rag import config


def test_retrieval_config_has_required_values():
    cfg = config.retrieval()
    assert cfg["collection"] == "qris_kb_v1"
    emb = cfg["embedding"]
    assert emb["model_id"] == "BAAI/bge-m3"
    assert len(emb["revision"]) == 40
    assert emb["dimension"] == 1024
    chunk = cfg["chunking"]
    assert 400 <= chunk["target_tokens"] <= chunk["max_tokens"] <= 800
    assert 60 <= chunk["overlap_tokens"] <= 100
    assert emb["max_seq_length"] >= chunk["max_tokens"]
    assert 20 <= cfg["search"]["limit"] <= min(cfg["search"]["dense_prefetch"], cfg["search"]["sparse_prefetch"])
    assert set(cfg["ingest"]["types"]) == {".pdf", ".md"}


def test_excerpt_limits_come_from_security_config():
    assert config.excerpt_limit("reference_only") == 300
    assert config.excerpt_limit("team_authored") == 1000


def test_unknown_permission_basis_has_no_limit():
    with pytest.raises(ValueError):
        config.excerpt_limit("licensed")


def test_qdrant_url_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    assert config.qdrant_url() == "http://127.0.0.1:6333"
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:7000")
    assert config.qdrant_url() == "http://127.0.0.1:7000"
