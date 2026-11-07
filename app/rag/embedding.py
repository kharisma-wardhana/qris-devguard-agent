"""Dense embedders behind one interface (p2 spec: BGE-M3 on CPU; fake in tests).

`BgeM3Embedder` needs the optional `embed` extra (`uv sync --extra embed`) and the
weights fetched once by `scripts/fetch_models.py`. It always loads offline
(HF_HUB_OFFLINE=1), so ingestion and search never reach the internet.

`FakeEmbedder` is deterministic and dependency-free: a normalised bag of hashed
tokens, with an optional synonym map so tests can make a "meaning-only" match.
"""

from __future__ import annotations

import math
import os
from typing import Protocol

from app.rag import config, sparse


class EmbedStackMissing(RuntimeError):
    """The optional embedding stack is not installed."""


class Embedder(Protocol):
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def count_tokens(self, text: str) -> int | None: ...


class FakeEmbedder:
    def __init__(self, dimension: int | None = None, synonyms: dict[str, str] | None = None) -> None:
        self.dimension = dimension or config.retrieval()["embedding"]["dimension"]
        self.synonyms = {k.lower(): v.lower() for k, v in (synonyms or {}).items()}

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token in sparse.tokenize(text):
            token = self.synonyms.get(token, token)
            vec[sparse.term_index(token) % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def count_tokens(self, text: str) -> int | None:
        return None


class BgeM3Embedder:
    def __init__(self) -> None:
        cfg = config.retrieval()["embedding"]
        os.environ["HF_HUB_OFFLINE"] = "1"            # run time never downloads
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbedStackMissing("embedding stack not installed: run `uv sync --extra embed`") from exc
        try:
            self.model = SentenceTransformer(cfg["model_id"], revision=cfg["revision"], device="cpu")
        except OSError as exc:
            raise EmbedStackMissing(
                f"{cfg['model_id']}@{cfg['revision'][:7]} is not in the local cache: "
                "run `uv run python scripts/fetch_models.py`") from exc
        self.model.max_seq_length = cfg["max_seq_length"]
        self.dimension = cfg["dimension"]
        self.batch_size = cfg["batch_size"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                 show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def count_tokens(self, text: str) -> int | None:
        return len(self.model.tokenizer(text, add_special_tokens=True)["input_ids"])
