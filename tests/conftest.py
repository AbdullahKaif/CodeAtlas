"""Shared fixtures for the CodeAtlas test-suite."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from git import Actor, Repo

from backend.config import settings
from backend.rag.embeddings import EmbeddingError, EmbeddingModel

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_REPO = FIXTURES_DIR / "sample_repo"

AUTHOR = Actor("CodeAtlas Tests", "tests@example.invalid")


@pytest.fixture
def temp_sessions(tmp_path, monkeypatch):
    """Point the session storage at a per-test temporary directory."""
    sessions_root = tmp_path / "temp"
    monkeypatch.setattr(settings, "temp_dir", sessions_root)
    return sessions_root


class FakeEmbeddingModel(EmbeddingModel):
    """Deterministic offline embedder: identical text -> identical unit vector.

    Retrieval quality is meaningless here; what tests get is determinism (a
    query equal to a passage scores ~1.0 on it) with no model download.
    """

    name = "fake-embed"

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for text in texts:
            seed = int.from_bytes(hashlib.md5(text.encode("utf-8")).digest()[:8], "big")
            vector = np.random.default_rng(seed).standard_normal(32).astype(np.float32)
            vectors.append(vector / np.linalg.norm(vector))
        return np.stack(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_passages([text])[0]


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    """Tests must never download/load a real model (slow, needs network).

    Any code path that asks for the default model gets a clean EmbeddingError -
    which the analyze pipeline treats as the optional-stage failure it is.
    Tests that want working retrieval use the fake_embeddings fixture; the
    opt-in real-model test passes its model explicitly and bypasses this.
    """

    def _refuse():
        raise EmbeddingError("Tests must not load a real embedding model; use fake_embeddings.")

    monkeypatch.setattr("backend.rag.retriever.get_embedding_model", _refuse)


@pytest.fixture
def fake_embeddings(_no_real_embeddings, monkeypatch):
    """Route every default-model lookup (indexing and querying) to the fake."""
    model = FakeEmbeddingModel()
    monkeypatch.setattr("backend.rag.retriever.get_embedding_model", lambda: model)
    return model


@pytest.fixture
def local_git_repo(tmp_path):
    """Create a small local git repository (clone source for tests, no network)."""
    src = tmp_path / "source_repo"
    src.mkdir()
    (src / "main.py").write_text('print("hello")\n', encoding="utf-8")
    (src / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src / "README.md").write_text("# Source repo\n", encoding="utf-8")
    repo = Repo.init(src)
    repo.index.add(["main.py", "util.py", "README.md"])
    repo.index.commit("initial commit", author=AUTHOR, committer=AUTHOR)
    repo.close()
    return src
