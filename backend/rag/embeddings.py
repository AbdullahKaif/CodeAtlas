"""Embedding abstraction over local Sentence Transformers models.

The model is configuration (CODEATLAS_EMBEDDING_MODEL), never hard-coded at a
call site, and everything runs locally on CPU. The abstract base exists so
tests - and the embedding evaluation planned in spec §15 - can swap models
without touching callers.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from backend.config import settings

logger = logging.getLogger(__name__)

# bge models are asymmetric: queries (not passages) get this instruction prefix.
# See the model card for BAAI/bge-small-en-v1.5.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingError(Exception):
    """Raised when the embedding model cannot be loaded or run."""


class EmbeddingModel:
    """Interface: embed passages and queries into L2-normalized float32 vectors."""

    name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray:  # (n, dim)
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:  # (dim,)
        raise NotImplementedError


class SentenceTransformerModel(EmbeddingModel):
    """Lazy-loading Sentence Transformers implementation.

    Loading is deferred until the first embed call so importing the backend
    never triggers a model download; the first analysis on a fresh machine
    does (~130 MB for the default model), which the caller should surface as
    its own stage rather than as silence.
    """

    def __init__(self, name: str | None = None) -> None:
        self.name = name or settings.embedding_model
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer

                        # Cached-first: once downloaded, the model loads with no
                        # network traffic at all (local-first promise). Only a
                        # cache miss falls through to the online path.
                        try:
                            logger.info("Loading embedding model %s (local cache)", self.name)
                            self._model = SentenceTransformer(
                                self.name, device="cpu", local_files_only=True
                            )
                        except Exception:
                            logger.info("Model %s not cached; downloading once", self.name)
                            self._model = SentenceTransformer(self.name, device="cpu")
                    except Exception as exc:
                        raise EmbeddingError(
                            f"Could not load embedding model '{self.name}'. First use needs "
                            f"internet access to download it; afterwards it runs offline. ({exc})"
                        ) from exc
        return self._model

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        try:
            vectors = model.encode(
                texts,
                batch_size=settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingError(f"Embedding failed with model '{self.name}': {exc}") from exc
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        prefix = _BGE_QUERY_PREFIX if "bge" in self.name.lower() else ""
        return self.embed_passages([prefix + text])[0]


_model_instance: EmbeddingModel | None = None
_instance_lock = threading.Lock()


def get_embedding_model() -> EmbeddingModel:
    """The process-wide embedding model for the configured name (cached)."""
    global _model_instance
    with _instance_lock:
        if _model_instance is None or _model_instance.name != settings.embedding_model:
            _model_instance = SentenceTransformerModel()
        return _model_instance
