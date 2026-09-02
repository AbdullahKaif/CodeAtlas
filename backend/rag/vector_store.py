"""FAISS-backed vector store, one per session, persisted under vectors/.

Cosine similarity via inner product on L2-normalized vectors (IndexFlatIP -
exact search; repositories index thousands of chunks, not millions, so ANN
structures would be unnecessary infrastructure). The dimension is never
hard-coded: it comes from the vectors at build time and from metadata at load
time, and metadata records which model produced the vectors so a query embedded
with a different model is refused instead of silently returning noise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import faiss
import numpy as np

INDEX_FILE = "index.faiss"
METADATA_FILE = "metadata.json"


class VectorStoreError(Exception):
    """Raised when an index is missing, corrupt, or mismatched with the query."""


class VectorStore:
    def __init__(self, index: faiss.Index, chunk_ids: list[str], model_name: str) -> None:
        self.index = index
        self.chunk_ids = chunk_ids
        self.model_name = model_name

    @property
    def dimension(self) -> int:
        return self.index.d

    @property
    def size(self) -> int:
        return self.index.ntotal

    @classmethod
    def build(cls, vectors: np.ndarray, chunk_ids: list[str], model_name: str) -> "VectorStore":
        if vectors.ndim != 2 or vectors.shape[0] != len(chunk_ids):
            raise VectorStoreError(
                f"Vector shape {vectors.shape} does not match {len(chunk_ids)} chunk ids."
            )
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(np.ascontiguousarray(vectors, dtype=np.float32))
        return cls(index, list(chunk_ids), model_name)

    def search(self, query: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        """Top-k (chunk_id, score) pairs, best first. Scores are cosine similarities."""
        if query.shape[-1] != self.dimension:
            raise VectorStoreError(
                f"Query dimension {query.shape[-1]} does not match index dimension {self.dimension} "
                f"(index was built with '{self.model_name}')."
            )
        top_k = min(top_k, self.size)
        if top_k <= 0:
            return []
        scores, positions = self.index.search(
            np.ascontiguousarray(query.reshape(1, -1), dtype=np.float32), top_k
        )
        return [
            (self.chunk_ids[position], float(score))
            for position, score in zip(positions[0], scores[0])
            if position >= 0
        ]

    def save(self, vectors_dir: Path) -> None:
        vectors_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(vectors_dir / INDEX_FILE))
        metadata = {
            "model": self.model_name,
            "dimension": self.dimension,
            "chunk_ids": self.chunk_ids,
        }
        tmp_path = vectors_dir / (METADATA_FILE + ".tmp")
        tmp_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(tmp_path, vectors_dir / METADATA_FILE)

    @classmethod
    def load(cls, vectors_dir: Path) -> "VectorStore":
        index_path = vectors_dir / INDEX_FILE
        metadata_path = vectors_dir / METADATA_FILE
        if not index_path.is_file() or not metadata_path.is_file():
            raise VectorStoreError("No vector index exists for this session.")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            index = faiss.read_index(str(index_path))
        except Exception as exc:
            raise VectorStoreError(f"Vector index could not be read: {exc}") from exc
        chunk_ids = metadata.get("chunk_ids", [])
        if index.ntotal != len(chunk_ids):
            raise VectorStoreError("Vector index and metadata are out of sync.")
        return cls(index, chunk_ids, metadata.get("model", "unknown"))
