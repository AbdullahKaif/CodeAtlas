"""Retrieval: embed chunks into a session's FAISS index, and query it.

Passages are embedded with a one-line ``file | symbol`` header prepended, so
location-flavoured questions ("where is authentication handled?") can match on
names and paths as well as on code content. The header is retrieval-only - the
chunk text returned to callers stays exactly its file lines.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

from backend.config import settings
from backend.knowledge.serializer import load_chunks
from backend.rag.embeddings import EmbeddingModel, get_embedding_model
from backend.rag.models import Chunk, IndexSummary, RetrievedChunk
from backend.rag.vector_store import VectorStore, VectorStoreError

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (chunks_done, chunks_total)


def passage_text(chunk: Chunk) -> str:
    header = chunk.file if chunk.symbol is None else f"{chunk.file} | {chunk.symbol}"
    return f"{header}\n{chunk.text}"


def embed_chunks(
    chunks: list[Chunk],
    model: EmbeddingModel | None = None,
    progress: ProgressCallback | None = None,
) -> np.ndarray:
    """Embed all chunk passages, in batches so progress is real, not guessed.

    Large repositories embed for minutes on CPU; the batch loop reports honest
    counts to the caller and to the server log, so neither the UI nor the
    terminal ever goes silent for the duration.
    """
    model = model or get_embedding_model()
    texts = [passage_text(c) for c in chunks]
    batch_size = settings.embedding_batch_size
    batches: list[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        batches.append(model.embed_passages(texts[start : start + batch_size]))
        done = min(start + batch_size, len(texts))
        if progress is not None:
            progress(done, len(texts))
        if done == len(texts) or (done // batch_size) % 10 == 0:
            logger.info("Embedded %d/%d chunks", done, len(texts))
    return np.vstack(batches)


def index_vectors(
    vectors_dir: Path, chunks: list[Chunk], vectors: np.ndarray, model_name: str
) -> IndexSummary:
    """Build and persist the session's FAISS index from already-embedded chunks."""
    store = VectorStore.build(vectors, [c.chunk_id for c in chunks], model_name)
    store.save(vectors_dir)
    return IndexSummary(chunks_indexed=store.size, dimension=store.dimension, model=model_name)


def build_index(
    vectors_dir: Path,
    chunks: list[Chunk],
    model: EmbeddingModel | None = None,
    progress: ProgressCallback | None = None,
) -> IndexSummary:
    """Embed all chunks and persist the session's FAISS index (one-shot helper)."""
    model = model or get_embedding_model()
    return index_vectors(vectors_dir, chunks, embed_chunks(chunks, model, progress), model.name)


def retrieve(
    session_dir: Path,
    question: str,
    top_k: int | None = None,
    model: EmbeddingModel | None = None,
) -> list[RetrievedChunk]:
    """Top-k chunks for a question, best first, with full chunk metadata.

    Raises VectorStoreError when the session has no usable index - including
    an index built by a different embedding model than the configured one,
    which would silently return noise if allowed through.
    """
    store = VectorStore.load(session_dir / "vectors")
    model = model or get_embedding_model()
    if store.model_name != model.name:
        raise VectorStoreError(
            f"This session's index was built with '{store.model_name}' but the configured "
            f"model is '{model.name}'. Re-analyze the repository to rebuild the index."
        )
    results = store.search(model.embed_query(question), top_k or settings.top_k)

    by_id = {c.chunk_id: c for c in load_chunks(session_dir / "analysis")}
    retrieved = []
    for chunk_id, score in results:
        chunk = by_id.get(chunk_id)
        if chunk is not None:  # ids in the index but missing from chunks.json are dropped
            retrieved.append(RetrievedChunk(**chunk.model_dump(), score=score))
    return retrieved
