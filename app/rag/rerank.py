"""Cross-encoder reranking of a hybrid search run (p3 spec Behaviour 2; AC #1-#3).

`rerank` only reorders: the output holds exactly the input candidates, with their
citation IDs and every §12.4 field unchanged, sorted by cross-encoder score (ties keep
the fused order). `select` takes the first `top_n` for the agent's context.

Source-version consistency (§12.3 step 6) is not applied here: it needs the user's
version context, which only the agent (P5) has. No version is ever dropped (§12.5).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from typing import Protocol

from app.rag import config, sparse
from app.rag.embedding import EmbedStackMissing
from app.rag.search import SearchRun


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...

    def count_tokens(self, query: str, text: str) -> int | None: ...


class FakeReranker:
    """Deterministic: share of distinct query terms present in the text, plus a tiny length tie-break."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        q = set(sparse.tokenize(query))
        out = []
        for text in texts:
            terms = set(sparse.tokenize(text))
            overlap = len(q & terms) / len(q) if q else 0.0
            out.append(round(overlap - len(text) * 1e-9, 12))
        return out

    def count_tokens(self, query: str, text: str) -> int | None:
        return None


class CrossEncoderReranker:
    def __init__(self) -> None:
        cfg = config.retrieval()["rerank"]
        os.environ["HF_HUB_OFFLINE"] = "1"                 # run time never downloads
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise EmbedStackMissing("embedding stack not installed: run `uv sync --extra embed`") from exc
        try:
            self.model = CrossEncoder(cfg["model_id"], revision=cfg["revision"], device="cpu",
                                      max_length=cfg["max_length"])
        except OSError as exc:
            raise EmbedStackMissing(
                f"{cfg['model_id']}@{cfg['revision'][:7]} is not in the local cache: "
                "run `uv run python scripts/fetch_models.py`") from exc
        self.batch_size = cfg["batch_size"]

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        scores = self.model.predict([(query, t) for t in texts], batch_size=self.batch_size,
                                    show_progress_bar=False)
        return [float(s) for s in scores]

    def count_tokens(self, query: str, text: str) -> int | None:
        return len(self.model.tokenizer(query, text)["input_ids"])


@dataclass(frozen=True)
class RerankStats:
    seconds: float
    pairs: int
    truncated_pairs: int | None


def rerank(run: SearchRun, reranker: Reranker) -> tuple[SearchRun, RerankStats]:
    """Reorder `run.results` by reranker score. Same candidates, same citation IDs, new ranks."""
    texts = [run.chunk_text[r.chunk_id] for r in run.results]
    started = time.perf_counter()
    scores = reranker.score(run.query, texts)
    seconds = time.perf_counter() - started
    if len(scores) != len(texts):
        raise RuntimeError(f"reranker returned {len(scores)} scores for {len(texts)} candidates")
    order = sorted(range(len(texts)), key=lambda i: (-scores[i], i))     # stable on ties: fused order
    results = [replace(run.results[i], rank=new_rank, rerank_score=scores[i])
               for new_rank, i in enumerate(order, start=1)]
    max_len = config.retrieval()["rerank"]["max_length"]
    counts = [reranker.count_tokens(run.query, t) for t in texts]
    truncated = None if not counts or counts[0] is None else sum(c > max_len for c in counts if c is not None)
    reranked = SearchRun(run.run_id, run.query, f"{run.mode}+rerank", results, dict(run.chunk_text))
    return reranked, RerankStats(seconds, len(texts), truncated)


def select(run: SearchRun, top_n: int | None = None) -> list:
    """The first `top_n` results (config `rerank.top_n`) for the agent's context."""
    n = config.retrieval()["rerank"]["top_n"] if top_n is None else top_n
    return run.results[:max(n, 0)]
