"""Tests for the knowledge index, architecture graph, impact analysis, onboarding and their endpoints."""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from backend.architecture.graph import UnknownFocusError, build_graph
from backend.config import settings
from backend.impact.analyzer import UnknownTargetError, analyze_impact
from backend.impact.explain import explain_impact
from backend.knowledge.builder import build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.knowledge.store import KnowledgeIndex, load_knowledge
from backend.main import app
from backend.onboarding.generator import generate_onboarding
from backend.onboarding.summary import summarize_repository
from backend.rag.chunker import build_chunks
from backend.rag.retriever import build_index
from backend.repository.scanner import scan_repository
from tests.conftest import SAMPLE_REPO, FakeEmbeddingModel, FakeLLMClient


@pytest.fixture(scope="module")
def fixture_index() -> KnowledgeIndex:
    scan = scan_repository(SAMPLE_REPO)
    kb = build_knowledge_base(SAMPLE_REPO, scan)
    readme = (SAMPLE_REPO / "README.md").read_text(encoding="utf-8")
    return KnowledgeIndex(kb.entities, kb.relationships, scan.files, "sample", readme_text=readme)


class TestKnowledgeIndex:
    def test_members_and_search(self, fixture_index):
        members = {e.id for e in fixture_index.members("app/auth.py::AuthService")}
        assert "app/auth.py::AuthService.login" in members and "app/auth.py::AuthService" in members
        file_members = {e.id for e in fixture_index.members("app/auth.py")}
        assert "app/auth.py::AuthService.login" in file_members  # nested through the class
        assert [e.id for e in fixture_index.search("login")][0] == "app/auth.py::AuthService.login"
        assert fixture_index.search("") == []
        assert fixture_index.is_test("tests/test_auth.py") and not fixture_index.is_test("app/auth.py")
        assert fixture_index.package_of("app/auth.py") == "app" and fixture_index.package_of("README.md") == "(root)"

    def test_load_knowledge_caches_by_mtime(self, temp_sessions):
        session_dir = settings.session_dir("abcdef123456")
        scan = scan_repository(SAMPLE_REPO)
        kb = build_knowledge_base(SAMPLE_REPO, scan)
        write_knowledge_base(session_dir / "analysis", kb)
        first = load_knowledge(session_dir)
        assert load_knowledge(session_dir) is first  # same mtime -> cached object
        assert first.entity("app/auth.py::AuthService") is not None
        assert first.files == {}  # no repository.json in this minimal session: still works


class TestArchitectureGraph:
    def test_file_graph_aggregates_edges(self, fixture_index):
        graph = build_graph(fixture_index)
        ids = {n.id for n in graph.nodes}
        assert {"app/auth.py", "app/database.py", "app/main.py", "tests/test_auth.py"} <= ids
        assert "README.md" not in ids  # only files with code entities are nodes
        edges = {(e.source, e.target, e.relation): e.count for e in graph.edges}
        assert edges[("app/auth.py", "app/base.py", "imports")] == 1
        assert edges[("app/auth.py", "app/base.py", "inherits")] == 1
        assert edges[("app/auth.py", "app/database.py", "calls")] >= 1
        assert graph.stats.level == "file" and not graph.stats.truncated
        assert graph.stats.total_edges == len(graph.edges)
        auth = next(n for n in graph.nodes if n.id == "app/auth.py")
        assert auth.classes == 1 and auth.functions >= 3 and auth.package == "app"
        main = next(n for n in graph.nodes if n.id == "app/main.py")
        assert main.is_entry_point
        assert "app" in graph.packages and "static" in graph.note.lower()

    def test_file_graph_truncates_by_degree(self, fixture_index):
        graph = build_graph(fixture_index, max_nodes=2)
        assert graph.stats.truncated and graph.stats.shown_nodes == 2
        assert all(e.source in {n.id for n in graph.nodes} for e in graph.edges)
        assert "app/auth.py" in {n.id for n in graph.nodes}  # the most connected file survives

    def test_focus_graph(self, fixture_index):
        graph = build_graph(fixture_index, focus="app/auth.py::AuthService", depth=1)
        ids = {n.id for n in graph.nodes}
        assert "app/auth.py::AuthService" in ids and "app/auth.py::AuthService.login" in ids
        assert "app/base.py::BaseService" in ids  # inherits
        assert "app/database.py::find_user" in ids  # called from login
        assert "app/main.py::run" in ids  # instantiates the class
        relations = {(e.source, e.target, e.relation) for e in graph.edges}
        assert ("app/auth.py::AuthService", "app/base.py::BaseService", "inherits") in relations
        assert ("app/auth.py::AuthService", "app/auth.py::AuthService.login", "contains") in relations
        assert graph.stats.level == "entity" and graph.stats.focus == "app/auth.py::AuthService"

    def test_unknown_focus(self, fixture_index):
        with pytest.raises(UnknownFocusError):
            build_graph(fixture_index, focus="nope.py::X")


class TestImpactAnalysis:
    def test_function_impact(self, fixture_index):
        result = analyze_impact(fixture_index, "app/database.py::find_user", depth=2)
        assert result.target.name == "find_user"
        direct = {a.id for a in result.affected if a.depth == 1}
        assert direct == {"app/auth.py::AuthService.login"}  # the one caller; importing app/database.py alone is not a dependency on find_user
        assert result.counts.callers == 1 and result.counts.importers == 0
        assert result.affected[0].line is not None  # the call site
        assert result.level == "MEDIUM"
        assert any("caller" in r for r in result.reasons)
        assert "lower bound" in result.note

    def test_class_impact_includes_members_and_tests(self, fixture_index):
        result = analyze_impact(fixture_index, "app/auth.py::AuthService", depth=2)
        assert result.target.members >= 3  # __init__, login, logout
        ids = {a.id for a in result.affected}
        assert "app/main.py::run" in ids and "tests/test_auth.py::test_login_unknown_user_fails" in ids
        assert "tests/test_auth.py" in result.tests
        assert result.counts.tests >= 1 and "app/main.py" in result.files
        assert all(a.id not in {"app/auth.py::AuthService.login"} for a in result.affected)  # members are not dependents

    def test_file_target_counts_importers(self, fixture_index):
        result = analyze_impact(fixture_index, "app/auth.py", depth=1)
        importers = {a.id for a in result.affected if a.via == "imports"}
        assert importers == {"app/main.py", "tests/test_auth.py"}
        assert result.counts.importers == 2 and result.target.members >= 4
        callers = {a.id for a in result.affected if a.via == "calls"}
        assert "app/main.py::run" in callers

    def test_isolated_target_is_low(self, fixture_index):
        result = analyze_impact(fixture_index, "app/auth.py::AuthService.logout", depth=3)
        assert result.level == "LOW" and result.affected == []
        assert "no static dependents" in " ".join(result.reasons)

    def test_unknown_target(self, fixture_index):
        with pytest.raises(UnknownTargetError):
            analyze_impact(fixture_index, "ghost.py::nothing")


class TestOnboarding:
    def test_guide_references_real_files(self, fixture_index):
        guide = generate_onboarding(fixture_index)
        assert guide.repository == "sample"
        assert guide.overview["entry_points"] == ["app/main.py"] and guide.overview["classes"] >= 2
        assert guide.overview["description"] and "fixture" in guide.overview["description"].lower()
        paths = {r.path for r in guide.important_files}
        assert "app/auth.py" in paths and "tests/test_auth.py" not in paths
        auth = next(r for r in guide.important_files if r.path == "app/auth.py")
        assert any("imported by" in reason for reason in auth.reasons) and "AuthService" in auth.symbols
        assert guide.reading_order[0].path == "README.md"
        assert any(step.path == "app/main.py" for step in guide.reading_order)
        assert all(step.path in fixture_index.files or step.path in fixture_index.by_file for step in guide.reading_order)
        names = {c.name for c in guide.key_concepts}
        assert "AuthService" in names and "BaseService" in names and "app" in names

        stages = {s.number: s for s in guide.stages}
        assert stages["03"].detected and "app/auth.py" in stages["03"].files
        assert stages["05"].detected and "app/database.py" in stages["05"].files
        assert stages["06"].detected and "tests/test_auth.py" in stages["06"].files
        assert all(s.questions for s in guide.stages)
        assert [d.day for d in guide.learning_path] == list(range(1, len(guide.learning_path) + 1))
        assert all(f in fixture_index.files or f in fixture_index.by_file for d in guide.learning_path for f in d.files)

    def test_undetected_stage_is_marked_not_invented(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        scan = scan_repository(repo)
        kb = build_knowledge_base(repo, scan)
        guide = generate_onboarding(KnowledgeIndex(kb.entities, kb.relationships, scan.files, "calc"))
        auth = next(s for s in guide.stages if s.number == "03")
        assert not auth.detected and auth.files == [] and "not detected" in auth.explanation.lower()
        assert all(day.files for day in guide.learning_path)


@pytest.fixture
def analyzed_session(temp_sessions):
    session_id = "abcdef123456"
    session_dir = settings.session_dir(session_id)
    repo = session_dir / "repository"
    shutil.copytree(SAMPLE_REPO, repo)
    scan = scan_repository(repo)
    kb = build_knowledge_base(repo, scan)
    chunks, _ = build_chunks(repo, scan, kb.entities)
    write_knowledge_base(session_dir / "analysis", kb)
    write_chunks(session_dir / "analysis", chunks)
    build_index(session_dir / "vectors", chunks, FakeEmbeddingModel())
    return session_id, session_dir


class TestImpactExplain:
    def test_explanation_uses_target_and_dependents(self, analyzed_session):
        session_id, session_dir = analyzed_session
        index = load_knowledge(session_dir)
        result = analyze_impact(index, "app/database.py::find_user", depth=2)
        seen = {}

        def _answer(prompt, system, history):
            seen["prompt"] = prompt
            return ("## What depends on it\nAuthService.login calls find_user.\n\n## Likely consequences of a change\n...\n\n"
                    "## What to check before changing it\n...\n\n## Tests to run\ntests/test_auth.py\n\n"
                    "Sources:\n- app/database.py: lines 12-23\n- app/auth.py: lines 13-19\n- app/ghost.py: lines 1-2")

        llm = FakeLLMClient(answer=_answer)
        explanation = explain_impact(session_dir, index, result, llm=llm)
        assert explanation.context[0].chunk_id == "target" and explanation.context[0].file == "app/database.py"
        assert any(c.entity_id == "app/auth.py::AuthService.login" for c in explanation.context)
        assert "Target: app/database.py::find_user" in seen["prompt"] and "Static impact level" in seen["prompt"]
        assert [s.file for s in explanation.sources] == ["app/database.py", "app/auth.py"]
        assert explanation.references_removed == 1 and not explanation.cached
        assert explain_impact(session_dir, index, result, llm=llm).cached is True and len(llm.calls) == 1


class TestRepositorySummary:
    def test_summary_is_cached(self, analyzed_session, fake_embeddings):
        session_id, session_dir = analyzed_session
        llm = FakeLLMClient(answer="It is a sample app.\n\nSources: none")
        first = summarize_repository(session_id, session_dir, llm=llm)
        assert first.summary == "It is a sample app." and not first.cached and first.context
        assert "joining the team" in llm.calls[0]["prompt"]
        assert summarize_repository(session_id, session_dir, llm=llm).cached is True and len(llm.calls) == 1
        assert summarize_repository(session_id, session_dir, refresh=True, llm=llm).cached is False


@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


class TestPhase6Endpoints:
    def test_unknown_and_running_sessions(self, client, temp_sessions):
        from backend.analysis.status import StatusTracker

        assert client.get("/api/architecture/0123456789ab").status_code == 404
        assert client.post("/api/impact", json={"session_id": "0123456789ab", "target": "x"}).status_code == 404
        assert client.get("/api/onboarding/0123456789ab").status_code == 404
        (temp_sessions / "session_abcdef123456").mkdir(parents=True)
        StatusTracker("abcdef123456", "repo", "https://github.com/x/repo")
        assert client.get("/api/architecture/abcdef123456").status_code == 409
        assert client.get("/api/repository/abcdef123456/entities?q=a").status_code == 409

    def test_architecture_endpoint(self, client, analyzed_session):
        session_id, _ = analyzed_session
        response = client.get(f"/api/architecture/{session_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["stats"]["level"] == "file" and any(n["id"] == "app/auth.py" for n in body["nodes"])
        focused = client.get(f"/api/architecture/{session_id}", params={"focus": "app/auth.py::AuthService", "depth": 2})
        assert focused.status_code == 200 and focused.json()["stats"]["level"] == "entity"
        assert client.get(f"/api/architecture/{session_id}", params={"focus": "nope"}).status_code == 404
        assert client.get(f"/api/architecture/{session_id}", params={"depth": 9}).status_code == 422

    def test_impact_endpoints(self, client, analyzed_session, fake_llm):
        session_id, _ = analyzed_session
        response = client.post("/api/impact", json={"session_id": session_id, "target": "app/auth.py::AuthService"})
        assert response.status_code == 200
        body = response.json()
        assert body["level"] in {"MEDIUM", "HIGH"} and body["tests"] == ["tests/test_auth.py"]
        assert client.post("/api/impact", json={"session_id": session_id, "target": "nope"}).status_code == 404

        fake_llm.answer = "## What depends on it\nx\n\nSources:\n- app/auth.py: lines 6-9"
        explained = client.post("/api/impact/explain", json={"session_id": session_id, "target": "app/auth.py::AuthService"})
        assert explained.status_code == 200, explained.text
        assert explained.json()["target"] == "app/auth.py::AuthService"

    def test_impact_explain_without_llm_is_503(self, client, analyzed_session):
        session_id, _ = analyzed_session
        response = client.post("/api/impact/explain", json={"session_id": session_id, "target": "app/auth.py::AuthService"})
        assert response.status_code == 503

    def test_onboarding_endpoints(self, client, analyzed_session, fake_embeddings, fake_llm):
        session_id, _ = analyzed_session
        response = client.get(f"/api/onboarding/{session_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["repository"] and len(body["stages"]) == 6 and body["reading_order"]
        assert body["overview"]["security"] is None  # no scan in this synthetic session

        fake_llm.answer = "A tiny sample application.\n\nSources: none"
        summary = client.post("/api/onboarding/summary", json={"session_id": session_id})
        assert summary.status_code == 200 and summary.json()["summary"] == "A tiny sample application."
        assert client.post("/api/onboarding/summary", json={"session_id": session_id}).json()["cached"] is True

    def test_entity_search(self, client, analyzed_session):
        session_id, _ = analyzed_session
        response = client.get(f"/api/repository/{session_id}/entities", params={"q": "login", "limit": 5})
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["id"] == "app/auth.py::AuthService.login" and results[0]["type"] == "method"
        top = client.get(f"/api/repository/{session_id}/entities", params={"types": "class,function"}).json()["results"]
        assert top and all(r["type"] in {"class", "function"} for r in top)
        assert top[0]["dependents"] >= top[-1]["dependents"]
