"""Shared fixtures for the CodeAtlas test-suite."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest
from git import Actor, Repo

from backend.config import settings
from backend.llm.ollama_client import ChatMessage, LLMClient, LLMHealth, LLMUnavailableError
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
    monkeypatch.setattr("backend.analysis.runner.get_embedding_model", _refuse)


@pytest.fixture
def fake_embeddings(_no_real_embeddings, monkeypatch):
    """Route every default-model lookup (indexing and querying) to the fake."""
    model = FakeEmbeddingModel()
    monkeypatch.setattr("backend.rag.retriever.get_embedding_model", lambda: model)
    monkeypatch.setattr("backend.analysis.runner.get_embedding_model", lambda: model)
    return model


class FakeLLMClient(LLMClient):
    """Offline stand-in for Ollama: returns a canned (or computed) answer and records calls."""

    name = "fake-llm"

    def __init__(self, answer="Answer.\n\nSources: none") -> None:
        self.answer = answer  # a string, or a callable (prompt, system, history) -> str
        self.calls: list[dict] = []

    def health_check(self) -> LLMHealth:
        return LLMHealth(
            reachable=True, base_url="fake://", model=self.name, model_available=True,
            available_models=[self.name], ready=True, message="fake model ready",
        )

    def model_available(self) -> bool:
        return True

    def generate(self, prompt, *, system=None, history: list[ChatMessage] | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system, "history": list(history or [])})
        if callable(self.answer):
            return self.answer(prompt, system, history)
        return self.answer


class _UnavailableLLMClient(LLMClient):
    name = "unavailable-llm"

    def health_check(self) -> LLMHealth:
        return LLMHealth(
            reachable=False, base_url="http://127.0.0.1:11434", model=self.name,
            model_available=False, ready=False, message="Ollama is not reachable (test guard).",
        )

    def model_available(self) -> bool:
        return False

    def generate(self, prompt, *, system=None, history=None) -> str:
        raise LLMUnavailableError("Ollama is not reachable (test guard); use fake_llm.")


_LLM_LOOKUPS = (
    "backend.rag.pipeline.get_llm_client",
    "backend.api.chat.get_llm_client",
    "backend.security.explain.get_llm_client",
)


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Tests must never contact a real Ollama server; the default client is 'unavailable'."""
    guard = _UnavailableLLMClient()
    for target in _LLM_LOOKUPS:
        monkeypatch.setattr(target, lambda: guard)


@pytest.fixture
def fake_llm(_no_real_llm, monkeypatch):
    """Route every default-LLM lookup to a recording fake. Set .answer per test."""
    client = FakeLLMClient()
    for target in _LLM_LOOKUPS:
        monkeypatch.setattr(target, lambda: client)
    return client


@pytest.fixture
def fake_clone(monkeypatch):
    """Replace the real git clone with a copy of the fixture repo (no network)."""
    import backend.analysis.runner as runner_module

    def _copy_fixture(url, dest, **kwargs):
        shutil.copytree(SAMPLE_REPO, dest, dirs_exist_ok=True)
        return dest

    monkeypatch.setattr(runner_module, "clone_repository", _copy_fixture)


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
