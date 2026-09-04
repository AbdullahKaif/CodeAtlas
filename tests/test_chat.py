"""Tests for the RAG pipeline: prompts, source-reference validation, chat endpoint (offline)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.rag.pipeline as pipeline_module
from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.main import app
from backend.rag.chunker import build_chunks
from backend.rag.models import RetrievedChunk
from backend.rag.pipeline import NO_EVIDENCE_ANSWER, answer_question
from backend.rag.prompts import SYSTEM_PROMPT, build_context, build_user_prompt
from backend.rag.retriever import build_index
from backend.rag.sources import extract_references, split_sources_section, validate_answer
from backend.repository.scanner import scan_repository
from tests.conftest import SAMPLE_REPO, FakeEmbeddingModel, FakeLLMClient


def chunk(chunk_id, file, start, end, symbol=None, type="function", score=0.9, text=None):
    return RetrievedChunk(
        chunk_id=chunk_id, file=file, symbol=symbol, entity_id=None, type=type,
        start_line=start, end_line=end, text=text or "\n".join(f"line {n}" for n in range(start, end + 1)),
        score=score,
    )


@pytest.fixture(scope="module")
def sample_entities():
    scan = scan_repository(SAMPLE_REPO)
    return build_knowledge_base(SAMPLE_REPO, scan).entities


CONTEXT = [
    chunk("chunk_00001", "app/auth.py", 12, 19, symbol="AuthService.login", type="method"),
    chunk("chunk_00002", "app/database.py", 13, 25, symbol="find_user", score=0.8),
    chunk("chunk_00003", "README.md", 1, 5, symbol="Sample Repo", type="documentation", score=0.5),
]


class TestExtractReferences:
    @pytest.mark.parametrize(
        "text",
        [
            "app/auth.py: lines 12-19",
            "app/auth.py: lines 12–19",  # en dash
            "app/auth.py, lines 12 to 19",
            "app/auth.py (lines 12-19)",
            "app/auth.py:12-19",
            "app/auth.py L12-L19",
            "app/auth.py line 12-19",
        ],
    )
    def test_line_range_formats(self, text):
        refs = extract_references(text)
        assert len(refs) == 1
        assert (refs[0].file, refs[0].start_line, refs[0].end_line) == ("app/auth.py", 12, 19)

    def test_single_line_and_symbol(self):
        refs = extract_references("see app/auth.py::AuthService.login line 14")
        assert refs[0].symbol == "AuthService.login"
        assert (refs[0].start_line, refs[0].end_line) == (14, 14)

    def test_bare_paths_only_when_requested(self):
        assert extract_references("look in app/auth.py please") == []
        refs = extract_references("- app/auth.py\n- README.md", bare_paths=True)
        assert [r.file for r in refs] == ["app/auth.py", "README.md"]
        assert refs[0].start_line is None


class TestSplitSources:
    def test_splits_trailing_section(self):
        body, sources = split_sources_section("Answer text.\n\nSources:\n- a.py: lines 1-2\n")
        assert body == "Answer text."
        assert sources == "- a.py: lines 1-2"

    def test_bold_and_none(self):
        body, sources = split_sources_section("Answer.\n\n**Sources:** none")
        assert body == "Answer." and sources == "none"

    def test_no_section(self):
        assert split_sources_section("Just an answer.") == ("Just an answer.", "")


class TestValidateAnswer:
    def test_valid_citation_kept_and_clamped(self, sample_entities):
        answer = "Login lives in app/auth.py.\n\nSources:\n- app/auth.py: lines 10-30"
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert result.answer == "Login lives in app/auth.py."  # Sources section stripped
        assert len(result.sources) == 1
        ref = result.sources[0]
        # Clamped to the chunk the model actually saw (12-19), never lines it did not.
        assert (ref.file, ref.start_line, ref.end_line) == ("app/auth.py", 12, 19)
        assert ref.symbol == "AuthService.login" and ref.chunk_id == "chunk_00001"
        assert result.references_removed == 0

    def test_file_not_in_context_is_removed(self, sample_entities):
        answer = "See app/main.py.\n\nSources:\n- app/main.py: lines 1-5"  # exists in repo, not shown
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert result.sources == [] and result.references_removed == 1

    def test_lines_outside_context_are_removed(self, sample_entities):
        answer = "Sources:\n- app/auth.py: lines 40-50"
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert result.sources == [] and result.references_removed == 1

    def test_unknown_symbol_is_removed_not_repaired(self, sample_entities):
        answer = "Sources:\n- app/auth.py::AuthService.register lines 12-19"
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert result.sources == [] and result.references_removed == 1

    def test_bare_file_in_sources_backed_by_context(self, sample_entities):
        answer = "Sources:\n- README.md\n- docs/missing.md"
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert [s.file for s in result.sources] == ["README.md"]
        assert result.sources[0].chunk_id == "chunk_00003"
        assert result.references_removed == 1

    def test_invalid_inline_citation_is_cut_from_body(self, sample_entities):
        answer = "Users are read by find_user (app/database.py: lines 13-25) and hashed in app/crypto.py: lines 1-9 too.\n\nSources: none"
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert "app/crypto.py" not in result.answer
        assert "(app/database.py: lines 13-25)" in result.answer  # valid inline reference kept
        assert result.answer.endswith("and hashed in too.")
        assert [s.file for s in result.sources] == ["app/database.py"]
        assert result.references_removed == 1

    def test_duplicates_listed_once(self, sample_entities):
        answer = "x (app/auth.py: lines 12-19)\n\nSources:\n- app/auth.py: lines 12-19"
        result = validate_answer(answer, CONTEXT, sample_entities)
        assert len(result.sources) == 1 and result.references_removed == 0


class TestPrompts:
    def test_context_blocks_carry_citable_headers(self):
        text, used = build_context(CONTEXT)
        assert used == CONTEXT
        assert "[1] app/auth.py: lines 12-19 (AuthService.login, method)" in text
        assert "[3] README.md: lines 1-5 (Sample Repo, documentation)" in text
        assert "```python" in text and "```markdown" in text

    def test_budget_drops_whole_chunks_best_first(self):
        big = chunk("c1", "a.py", 1, 100, text="x" * 3000, score=0.9)
        small = chunk("c2", "b.py", 1, 2, text="y", score=0.8)
        text, used = build_context([big, small], max_chars=3100)
        assert [c.chunk_id for c in used] == ["c1", "c2"]  # small one still fits after big
        _, used = build_context([big, chunk("c3", "c.py", 1, 50, text="z" * 2000, score=0.7), small], max_chars=3100)
        assert [c.chunk_id for c in used] == ["c1", "c2"]  # middle one dropped, never truncated

    def test_first_chunk_is_truncated_rather_than_sending_nothing(self):
        big = chunk("c1", "a.py", 1, 100, text="x" * 5000)
        text, used = build_context([big], max_chars=1000)
        assert used == [big] and len(text) <= 1000

    def test_user_prompt(self):
        assert build_user_prompt("Q?", "CTX").endswith("Question: Q?")
        assert "No repository context" in build_user_prompt("Q?", "")
        assert "Sources:" in SYSTEM_PROMPT


@pytest.fixture
def indexed_session(tmp_path):
    scan = scan_repository(SAMPLE_REPO)
    kb = build_knowledge_base(SAMPLE_REPO, scan)
    chunks, _ = build_chunks(SAMPLE_REPO, scan, kb.entities)
    session_dir = tmp_path / "session_x"
    write_knowledge_base(session_dir / "analysis", kb)
    write_chunks(session_dir / "analysis", chunks)
    build_index(session_dir / "vectors", chunks, FakeEmbeddingModel())
    return session_dir, chunks


class TestAnswerQuestion:
    def test_grounded_answer_with_validated_sources(self, indexed_session):
        session_dir, _ = indexed_session
        shown: dict = {}

        def _answer(prompt, system, history):
            shown["prompt"], shown["system"] = prompt, system
            first = prompt.split("\n")[2]  # "[1] file: lines a-b (...)"
            cite = first.split("] ", 1)[1].split(" (")[0]
            return f"It works like this.\n\nSources:\n- {cite}\n- app/ghost.py: lines 1-3"

        llm = FakeLLMClient(answer=_answer)
        result = answer_question(
            "sess", session_dir, "How does login work?", top_k=3, llm=llm, embedding_model=FakeEmbeddingModel()
        )
        assert result.answer == "It works like this."
        assert len(result.sources) == 1 and result.references_removed == 1
        assert result.sources[0].file == result.context[0].file
        assert len(result.context) == 3 and result.model == "fake-llm"
        assert shown["system"] == SYSTEM_PROMPT
        assert "Repository context:" in shown["prompt"] and "How does login work?" in shown["prompt"]

    def test_history_is_trimmed(self, indexed_session, monkeypatch):
        from backend.config import settings
        from backend.llm.ollama_client import ChatMessage

        monkeypatch.setattr(settings, "chat_history_turns", 2)
        session_dir, _ = indexed_session
        llm = FakeLLMClient()
        history = [ChatMessage(role="user", content=f"q{i}") for i in range(5)]
        answer_question("s", session_dir, "q", history=history, llm=llm, embedding_model=FakeEmbeddingModel())
        assert [m.content for m in llm.calls[0]["history"]] == ["q3", "q4"]

    def test_no_evidence_skips_the_llm(self, indexed_session, monkeypatch):
        session_dir, _ = indexed_session
        monkeypatch.setattr(pipeline_module, "retrieve", lambda *a, **k: [])
        llm = FakeLLMClient()
        result = answer_question("s", session_dir, "q", llm=llm, embedding_model=FakeEmbeddingModel())
        assert result.answer == NO_EVIDENCE_ANSWER and llm.calls == [] and result.sources == []


@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


def analyzed_session(client) -> str:
    started = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
    assert started.status_code == 202
    return started.json()["session_id"]


class TestChatEndpoint:
    def test_llm_health_reports_setup_state(self, client):
        response = client.get("/api/llm/health")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is False and "not reachable" in body["message"]

    def test_unknown_session_is_404(self, client):
        assert client.post("/api/chat", json={"session_id": "0123456789ab", "question": "x"}).status_code == 404

    def test_invalid_body_is_422(self, client):
        assert client.post("/api/chat", json={"session_id": "abc", "question": ""}).status_code == 422
        bad_history = {"session_id": "abc", "question": "x", "history": [{"role": "system", "content": "x"}]}
        assert client.post("/api/chat", json=bad_history).status_code == 422

    def test_session_without_index_is_409(self, client, fake_clone, fake_llm):
        session_id = analyzed_session(client)  # no fake_embeddings: index never built
        response = client.post("/api/chat", json={"session_id": session_id, "question": "x"})
        assert response.status_code == 409

    def test_llm_unavailable_is_503_with_setup_message(self, client, fake_clone, fake_embeddings):
        session_id = analyzed_session(client)
        response = client.post("/api/chat", json={"session_id": session_id, "question": "x"})
        assert response.status_code == 503
        assert "Ollama" in response.json()["detail"]

    def test_full_chat_flow(self, client, fake_clone, fake_embeddings, fake_llm):
        session_id = analyzed_session(client)
        fake_llm.answer = "Authentication is in AuthService.\n\nSources:\n- app/auth.py: lines 1-200\n- nope/x.py: lines 1-2"
        response = client.post(
            "/api/chat",
            json={
                "session_id": session_id,
                "question": "How does authentication work?",
                "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
                "top_k": 20,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"] == "Authentication is in AuthService."
        assert body["model"] == "fake-llm" and len(body["context"]) > 0
        assert body["references_removed"] == 1
        assert [s["file"] for s in body["sources"]] == ["app/auth.py"]
        assert body["sources"][0]["chunk_id"].startswith("chunk_")
        assert len(fake_llm.calls[0]["history"]) == 2
