"""Analysis session endpoints: start a background analysis, poll it, read it, delete it."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from backend.analysis.runner import AnalyzeResponse, RepositoryInfo, run_analysis
from backend.analysis.status import AnalysisStatus, StatusTracker, load_status
from backend.privacy.cleanup import delete_session
from backend.repository.clone import (
    InvalidRepoURLError,
    create_session,
    get_session_dir,
    repo_name_from_url,
    validate_github_url,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(..., description="HTTPS GitHub repository URL", examples=["https://github.com/pallets/flask"])


class AnalyzeStartedResponse(BaseModel):
    """Analysis runs in the background; poll /api/analysis/{session_id}/status."""

    session_id: str
    repository: RepositoryInfo
    state: Literal["running"] = "running"


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool


@router.post("/analyze", response_model=AnalyzeStartedResponse, status_code=202)
async def analyze_repository(request: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeStartedResponse:
    """Validate the URL, create an isolated session, and start the analysis."""
    try:
        url = validate_github_url(request.repo_url)
    except InvalidRepoURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id, repo_dir = create_session()
    repo_name = repo_name_from_url(url)
    # The tracker (and status.json) must exist before the response goes out,
    # so a client polling immediately never sees a 404 for a real session.
    tracker = StatusTracker(session_id, repo_name, url)
    background_tasks.add_task(run_analysis, session_id, url, repo_dir, repo_name, tracker)
    return AnalyzeStartedResponse(
        session_id=session_id, repository=RepositoryInfo(name=repo_name, url=url)
    )


@router.get("/analysis/{session_id}/status", response_model=AnalysisStatus)
def get_analysis_status(session_id: str) -> AnalysisStatus:
    """Real per-stage progress of a session's analysis (never faked)."""
    if get_session_dir(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    status = load_status(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="No analysis status for this session.")
    return status


@router.get("/repository/{session_id}/overview", response_model=AnalyzeResponse)
def get_overview(session_id: str) -> AnalyzeResponse:
    """Return the persisted analysis overview for a completed session."""
    session_dir = get_session_dir(session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    overview_path = session_dir / "analysis" / "repository.json"
    if not overview_path.is_file():
        status = load_status(session_id)
        if status is not None and status.state == "running":
            raise HTTPException(status_code=409, detail="Analysis is still running.")
        if status is not None and status.state == "failed":
            raise HTTPException(status_code=409, detail=status.error or "Analysis failed.")
        raise HTTPException(status_code=404, detail="No analysis found for this session.")
    try:
        return AnalyzeResponse.model_validate_json(overview_path.read_text(encoding="utf-8"))
    except ValueError:
        logger.exception("Corrupt overview for session %s", session_id)
        raise HTTPException(status_code=500, detail="Stored analysis is corrupt. Re-analyze the repository.")


@router.delete("/session/{session_id}", response_model=DeleteSessionResponse)
def delete_session_endpoint(session_id: str) -> DeleteSessionResponse:
    """Delete all temporary data (cloned repo, analysis, indexes) for a session."""
    try:
        existed = delete_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError:
        logger.exception("Failed to delete session %s", session_id)
        raise HTTPException(status_code=500, detail="Could not fully delete session data. Try again.")
    if not existed:
        raise HTTPException(status_code=404, detail="Session not found.")
    return DeleteSessionResponse(session_id=session_id, deleted=True)
