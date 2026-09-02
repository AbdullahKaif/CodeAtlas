"""Tests for the vector store, retriever, and search endpoint (offline via fake model)."""
from __future__ import annotations

import os

import numpy as np
import pytest

from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import write_chunks
from backend.rag.chunker import build_chunks
from backend.rag.retriever import build_index, passage_text, retrieve
from backend.rag.vector_store import VectorStore, VectorStoreError
from backend.repository.scanner import scan_repository
from tests.conftest import SAMPLE_REPO, FakeEmbeddingModel


def unit_rows(rows: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(rows, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


class TestVectorStore:
    VECTORS = unit_rows([[1, 0, 0], [0, 1, 0], [0.9, 0.1, 0]])
    IDS = ["chunk_00001", "chunk_00002", "chunk_00003"]

    def test_search_orders_by_similarity(self):
        store = VectorStore.build(self.VECTORS, self.IDS, "m")
        results = store.search(unit_rows([[1, 0, 0]])[0], top_k=3)
        assert [chunk_id for chunk_id, _ in results] == ["chunk_00001", "chunk_00003", "chunk_00002"]
        assert results[0][1] == pytest.approx(1.0)

    def test_top_k_is_clamped_to_index_size(self):
        store = VectorStore.build(self.VECTORS, self.IDS, "m")
        assert len(store.search(self.VECTORS[0], top_k=50)) == 3

    def test_save_load_round_trip(self, tmp_path):
        VectorStore.build(self.VECTORS, self.IDS, "model-x").save(tmp_path)
        loaded = VectorStore.load(tmp_path)
        assert loaded.model_name == "model-x"
        assert loaded.dimension == 3  # from metadata, never hard-coded
        assert loaded.search(self.VECTORS[1], 1)[0][0] == "chunk_00002"

    def test_missing_index_raises(self, tmp_path):
        with pytest.raises(VectorStoreError, match="No vector index"):
            VectorStore.load(tmp_path)

    def test_query_dimension_mismatch_raises(self):
        store = VectorStore.build(self.VECTORS, self.IDS, "m")
        with pytest.raises(VectorStoreError, match="dimension"):
            store.search(np.ones(5, dtype=np.float32), 1)

    def test_out_of_sync_metadata_raises(self, tmp_path):
        import json

        VectorStore.build(self.VECTORS, self.IDS, "m").save(tmp_path)
        metadata_path = tmp_path / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["chunk_ids"] = metadata["chunk_ids"][:2]  # one id fewer than vectors
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with pytest.raises(VectorStoreError, match="out of sync"):
            VectorStore.load(tmp_path)


@pytest.fixture
def indexed_session(tmp_path):
    """A session dir with the sample repo chunked and indexed by the fake model."""
    scan = scan_repository(SAMPLE_REPO)
    kb = build_knowledge_base(SAMPLE_REPO, scan)
    chunks, _ = build_chunks(SAMPLE_REPO, scan, kb.entities)
    session_dir = tmp_path / "session_x"
    write_chunks(session_dir / "analysis", chunks)
    build_index(session_dir / "vectors", chunks, FakeEmbeddingModel())
    return session_dir, chunks


class TestRetriever:
    def test_exact_passage_query_retrieves_its_chunk(self, indexed_session):
        session_dir, chunks = indexed_session
        target = next(c for c in chunks if c.entity_id == "app/auth.py::AuthService.login")
        results = retrieve(session_dir, passage_text(target), model=FakeEmbeddingModel())
        assert results[0].chunk_id == target.chunk_id
        assert results[0].score == pytest.approx(1.0, abs=1e-5)
        assert results[0].file == "app/auth.py"
        assert results[0].start_line == target.start_line  # metadata survives retrieval

    def test_results_are_sorted_and_bounded(self, indexed_session):
        session_dir, chunks = indexed_session
        results = retrieve(session_dir, "anything", top_k=3, model=FakeEmbeddingModel())
        assert len(results) == 3
        assert results[0].score >= results[1].score >= results[2].score

    def test_model_mismatch_is_refused(self, indexed_session):
        session_dir, _ = indexed_session
        other = FakeEmbeddingModel()
        other.name = "different-model"
        with pytest.raises(VectorStoreError, match="Re-analyze"):
            retrieve(session_dir, "anything", model=other)


class TestRealEmbeddingModel:
    """Runs only when explicitly requested: needs the real model (downloads once)."""

    pytestmark = pytest.mark.skipif(
        os.environ.get("CODEATLAS_TEST_REAL_EMBEDDINGS") != "1",
        reason="set CODEATLAS_TEST_REAL_EMBEDDINGS=1 to run the real-model test",
    )

    def test_real_model_retrieves_authentication_code(self, tmp_path):
        from backend.rag.embeddings import SentenceTransformerModel

        scan = scan_repository(SAMPLE_REPO)
        kb = build_knowledge_base(SAMPLE_REPO, scan)
        chunks, _ = build_chunks(SAMPLE_REPO, scan, kb.entities)
        session_dir = tmp_path / "session_real"
        write_chunks(session_dir / "analysis", chunks)
        model = SentenceTransformerModel()
        build_index(session_dir / "vectors", chunks, model)

        results = retrieve(session_dir, "How does user authentication work?", top_k=3, model=model)
        assert any("auth" in r.file for r in results), [r.file for r in results]