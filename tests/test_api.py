"""API-level tests using FastAPI's TestClient (no network access needed).

Analysis runs as a FastAPI background task; TestClient executes those tasks
before the response is handed back, so by the time a POST /api/analyze call
returns, the analysis has already finished - tests can assert on the final
status immediately without polling.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.analysis.runner as runner_module
from backend.analysis.status import StatusTracker
from backend.main import app
from backend.repository.clone import GitCloneError


@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestAnalyze:
    def test_invalid_url_is_400(self, client):
        response = client.post("/api/analyze", json={"repo_url": "https://gitlab.com/x/y"})
        assert response.status_code == 400
        assert "github.com" in response.json()["detail"]

    def test_missing_body_is_422(self, client):
        response = client.post("/api/analyze", json={})
        assert response.status_code == 422

    def test_analyze_full_flow(self, client, fake_clone, fake_embeddings, temp_sessions):
        started = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
        assert started.status_code == 202
        assert started.json()["state"] == "running"
        assert started.json()["repository"]["name"] == "sample"
        session_id = started.json()["session_id"]

        status = client.get(f"/api/analysis/{session_id}/status").json()
        assert status["state"] == "completed"
        assert [s["name"] for s in status["stages"]][-1] == "security"
        assert [s["state"] for s in status["stages"]][:6] == ["completed"] * 6
        # The security stage depends on which scanners are installed on this
        # machine; either way it must have settled, never stayed pending.
        assert status["stages"][-1]["state"] in {"completed", "failed"}
        embedding = next(s for s in status["stages"] if s["name"] == "embedding")
        assert "chunks" in embedding["detail"]  # real counts, not faked progress

        overview = client.get(f"/api/repository/{session_id}/overview")
        assert overview.status_code == 200
        body = overview.json()
        assert body["scan"]["summary"]["files_included"] > 0
        assert any(f["path"] == "app/auth.py" for f in body["scan"]["files"])
        assert body["parse"]["files_parsed"] >= 5
        assert body["parse"]["entities"]["class"] >= 2
        assert body["chunks"]["total"] > 0
        assert body["index"]["chunks_indexed"] == body["chunks"]["total"]
        assert body["index"]["model"] == "fake-embed"
        assert body["index_error"] is None
        assert {s["name"] for s in body["security"]["scanners"]} == {"semgrep", "gitleaks"}

        analysis_dir = temp_sessions / f"session_{session_id}" / "analysis"
        assert (analysis_dir / "entities.json").exists()
        assert (analysis_dir / "chunks.json").exists()
        assert (temp_sessions / f"session_{session_id}" / "vectors" / "index.faiss").exists()

        search = client.post(
            "/api/search", json={"session_id": session_id, "question": "authentication", "top_k": 3}
        )
        assert search.status_code == 200
        results = search.json()["results"]
        assert len(results) == 3
        assert {"chunk_id", "file", "start_line", "score", "text"} <= set(results[0])

        deleted = client.delete(f"/api/session/{session_id}")
        assert deleted.status_code == 200
        assert not (temp_sessions / f"session_{session_id}").exists()
        assert client.get(f"/api/repository/{session_id}/overview").status_code == 404
        assert client.get(f"/api/analysis/{session_id}/status").status_code == 404

    def test_clone_failure_fails_status_and_scrubs_content(self, client, temp_sessions, monkeypatch):
        def _boom(url, dest, **kwargs):
            raise GitCloneError("Repository not found.")

        monkeypatch.setattr(runner_module, "clone_repository", _boom)
        started = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/missing"})
        assert started.status_code == 202
        session_id = started.json()["session_id"]

        status = client.get(f"/api/analysis/{session_id}/status").json()
        assert status["state"] == "failed"
        assert "not found" in status["error"].lower()
        assert next(s for s in status["stages"] if s["name"] == "cloning")["state"] == "failed"

        # Repository content is scrubbed; only status.json remains for the UI.
        session_dir = temp_sessions / f"session_{session_id}"
        assert not (session_dir / "repository").exists()
        assert (session_dir / "status.json").exists()
        # The failure is also visible through the overview endpoint:
        assert client.get(f"/api/repository/{session_id}/overview").status_code == 409

    def test_persist_failure_fails_status_and_scrubs(self, client, fake_clone, fake_embeddings, temp_sessions, monkeypatch):
        def _disk_full(session_id, response, knowledge, chunk_list):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(runner_module, "_persist", _disk_full)
        started = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
        session_id = started.json()["session_id"]
        status = client.get(f"/api/analysis/{session_id}/status").json()
        assert status["state"] == "failed"
        assert not (temp_sessions / f"session_{session_id}" / "repository").exists()


class TestStatusEndpoint:
    def test_unknown_session_is_404(self, client):
        assert client.get("/api/analysis/0123456789ab/status").status_code == 404

    def test_malformed_session_is_404(self, client):
        assert client.get("/api/analysis/../../etc/status").status_code == 404

    def test_overview_while_running_is_409(self, client, temp_sessions):
        """A session mid-analysis (no repository.json yet) reports 'still running'."""
        session_dir = temp_sessions / "session_abcdef123456"
        session_dir.mkdir(parents=True)
        StatusTracker("abcdef123456", "repo", "https://github.com/x/repo")
        response = client.get("/api/repository/abcdef123456/overview")
        assert response.status_code == 409
        assert "running" in response.json()["detail"].lower()


class TestEmbeddingDegradation:
    def test_analysis_succeeds_without_embedding_model(self, client, fake_clone):
        """No fake_embeddings here: the autouse guard makes the model unavailable,
        which must degrade search - never the analysis (spec §42)."""
        started = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
        session_id = started.json()["session_id"]

        status = client.get(f"/api/analysis/{session_id}/status").json()
        assert status["state"] == "completed"  # analysis itself succeeded
        assert next(s for s in status["stages"] if s["name"] == "embedding")["state"] == "failed"

        body = client.get(f"/api/repository/{session_id}/overview").json()
        assert body["index"] is None
        assert "embedding model" in body["index_error"].lower()

        search = client.post("/api/search", json={"session_id": session_id, "question": "x"})
        assert search.status_code == 409


class TestSearchEndpoint:
    def test_unknown_session_is_404(self, client):
        response = client.post("/api/search", json={"session_id": "0123456789ab", "question": "x"})
        assert response.status_code == 404

    def test_invalid_body_is_422(self, client):
        assert client.post("/api/search", json={"session_id": "abc"}).status_code == 422
        assert (
            client.post(
                "/api/search", json={"session_id": "abc", "question": "x", "top_k": 0}
            ).status_code
            == 422
        )


class TestSessionEndpoints:
    def test_overview_unknown_session_is_404(self, client):
        assert client.get("/api/repository/0123456789ab/overview").status_code == 404

    def test_overview_malformed_session_is_404(self, client):
        assert client.get("/api/repository/../../etc/overview").status_code == 404

    def test_delete_malformed_session_is_400(self, client):
        assert client.delete("/api/session/notvalid!").status_code == 400