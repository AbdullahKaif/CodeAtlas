"""API-level tests using FastAPI's TestClient (no network access needed)."""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

import backend.api.analyze as analyze_module
from backend.main import app
from backend.repository.clone import GitCloneError
from tests.conftest import SAMPLE_REPO


@pytest.fixture
def client(temp_sessions):
    return TestClient(app)


@pytest.fixture
def fake_clone(monkeypatch):
    """Replace the real git clone with a copy of the fixture repo."""

    def _copy_fixture(url, dest, **kwargs):
        shutil.copytree(SAMPLE_REPO, dest, dirs_exist_ok=True)
        return dest

    monkeypatch.setattr(analyze_module, "clone_repository", _copy_fixture)


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

    def test_analyze_full_flow(self, client, fake_clone, temp_sessions):
        response = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
        assert response.status_code == 200
        body = response.json()
        assert body["repository"]["name"] == "sample"
        assert body["scan"]["summary"]["files_included"] > 0
        assert any(f["path"] == "app/auth.py" for f in body["scan"]["files"])
        assert body["parse"]["files_parsed"] >= 5
        assert body["parse"]["files_failed"] == 0
        assert body["parse"]["entities"]["class"] >= 2
        assert body["chunks"]["total"] > 0
        assert body["chunks"]["by_type"]["method"] >= 4

        session_id = body["session_id"]
        analysis_dir = temp_sessions / f"session_{session_id}" / "analysis"
        assert (analysis_dir / "entities.json").exists()
        assert (analysis_dir / "relationships.json").exists()
        assert (analysis_dir / "chunks.json").exists()
        overview = client.get(f"/api/repository/{session_id}/overview")
        assert overview.status_code == 200
        assert overview.json()["session_id"] == session_id

        deleted = client.delete(f"/api/session/{session_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert not (temp_sessions / f"session_{session_id}").exists()

        assert client.get(f"/api/repository/{session_id}/overview").status_code == 404
        assert client.delete(f"/api/session/{session_id}").status_code == 404

    def test_clone_failure_cleans_up_session(self, client, temp_sessions, monkeypatch):
        def _boom(url, dest, **kwargs):
            raise GitCloneError("Repository not found.")

        monkeypatch.setattr(analyze_module, "clone_repository", _boom)
        response = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/missing"})
        assert response.status_code == 502
        leftovers = list(temp_sessions.glob("session_*")) if temp_sessions.exists() else []
        assert leftovers == []


class TestSessionEndpoints:
    def test_overview_unknown_session_is_404(self, client):
        assert client.get("/api/repository/0123456789ab/overview").status_code == 404

    def test_overview_malformed_session_is_404(self, client):
        assert client.get("/api/repository/../../etc/overview").status_code == 404

    def test_delete_malformed_session_is_400(self, client):
        assert client.delete("/api/session/notvalid!").status_code == 400


class TestPersistFailure:
    def test_persist_failure_cleans_up_and_returns_500(self, client, fake_clone, temp_sessions, monkeypatch):
        """Review finding: a failed persist must not orphan the cloned repo."""

        def _disk_full(session_id, response, knowledge, chunk_list):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(analyze_module, "_persist_analysis", _disk_full)
        response = client.post("/api/analyze", json={"repo_url": "https://github.com/octo/sample"})
        assert response.status_code == 500
        assert "store" in response.json()["detail"].lower()
        leftovers = list(temp_sessions.glob("session_*")) if temp_sessions.exists() else []
        assert leftovers == []
