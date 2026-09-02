"""Retrieval: embed chunks into a session's FAISS index, and query it.

Passages are embedded with a one-line ``file | symbol`` header prepended, so
location-flavoured questions ("where is authentication handled?") can match on
names and paths as well as on code content. The header is retrieval-only - the
chunk text returned to callers stays exactly its file lines.
"""
from __future__ import annotations

from pathlib import Path

from backend.config import settings
from backend.knowledge.serializer import load_chunks
from backend.rag.embeddings import EmbeddingModel, get_embedding_model
from backend.rag.models import Chunk, IndexSummary, RetrievedChunk
from backend.rag.vector_store import VectorStore, VectorStoreError


def passage_text(chunk: Chunk) -> str:
    header = chunk.file if chunk.symbol is None else f"{chunk.file} | {chunk.symbol}"
    return f"{header}\n{chunk.text}"


def build_index(vectors_dir: Path, chunks: list[Chunk], model: EmbeddingModel | None = None) -> IndexSummary:
    """Embed all chunks and persist the session's FAISS index."""
    model = model or get_embedding_model()
    vectors = model.embed_passages([passage_text(c) for c in chunks])
    store = VectorStore.build(vectors, [c.chunk_id for c in chunks], model.name)
    store.save(vectors_dir)
    return IndexSummary(chunks_indexed=store.size, dimension=store.dimension, model=model.name)


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
