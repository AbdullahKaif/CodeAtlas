"""Analysis session endpoints: create a session (clone + scan), read it back, delete it."""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.config import settings
from backend.knowledge.builder import KnowledgeBase, build_knowledge_base
from backend.knowledge.serializer import write_chunks, write_knowledge_base
from backend.parser.models import ParseSummary
from backend.rag.chunker import build_chunks
from backend.rag.models import Chunk, ChunkSummary
from backend.privacy.cleanup import delete_session
from backend.repository.clone import (
    CloneError,
    GitCloneError,
    InvalidRepoURLError,
    RepoTooLargeError,
    clone_repository,
    create_session,
    get_session_dir,
    repo_name_from_url,
    validate_github_url,
)
from backend.repository.scanner import RepositoryScan, scan_repository

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(..., description="HTTPS GitHub repository URL", examples=["https://github.com/pallets/flask"])


class RepositoryInfo(BaseModel):
    name: str
    url: str


class AnalyzeResponse(BaseModel):
    session_id: str
    repository: RepositoryInfo
    scan: RepositoryScan
    # None only for sessions persisted before the respective stage existed.
    parse: ParseSummary | None = None
    chunks: ChunkSummary | None = None


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_repository(request: AnalyzeRequest) -> AnalyzeResponse:
    """Clone a GitHub repository into an isolated session and scan its files."""
    try:
        url = validate_github_url(request.repo_url)
    except InvalidRepoURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id, repo_dir = create_session()
    try:
        scan, knowledge, chunk_list, chunk_summary = await run_in_threadpool(
            _clone_scan_parse, url, repo_dir
        )
    except RepoTooLargeError as exc:
        _cleanup_quietly(session_id)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except GitCloneError as exc:
        _cleanup_quietly(session_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except CloneError as exc:
        _cleanup_quietly(session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception:
        logger.exception("Unexpected failure while analyzing session %s", session_id)
        _cleanup_quietly(session_id)
        raise HTTPException(status_code=500, detail="Repository analysis failed unexpectedly.")

    if scan.summary.files_included == 0:
        _cleanup_quietly(session_id)
        raise HTTPException(status_code=422, detail="The repository contains no analyzable source files.")

    response = AnalyzeResponse(
        session_id=session_id,
        repository=RepositoryInfo(name=repo_name_from_url(url), url=url),
        scan=scan,
        parse=knowledge.summary,
        chunks=chunk_summary,
    )
    try:
        _persist_analysis(session_id, response, knowledge, chunk_list)
    except OSError as exc:
        # Without cleanup here the client would never learn the session id and
        # the cloned repository would be orphaned on disk - a privacy violation.
        logger.exception("Could not persist analysis for session %s", session_id)
        _cleanup_quietly(session_id)
        raise HTTPException(status_code=500, detail="Could not store analysis results locally.") from exc
    return response


def _clone_scan_parse(
    url: str, repo_dir
) -> tuple[RepositoryScan, KnowledgeBase, list[Chunk], ChunkSummary]:
    """The synchronous analysis stages, in order: clone -> scan -> parse -> chunk.

    Kept as one ordered sequence so the Phase 3 move to background jobs with
    per-stage progress is a refactor of this function, not of the endpoint.
    """
    clone_repository(url, repo_dir)
    scan = scan_repository(repo_dir)
    knowledge = build_knowledge_base(repo_dir, scan)
    chunk_list, chunk_summary = build_chunks(repo_dir, scan, knowledge.entities)
    return scan, knowledge, chunk_list, chunk_summary


def _persist_analysis(
    session_id: str, response: AnalyzeResponse, knowledge: KnowledgeBase, chunk_list: list[Chunk]
) -> None:
    """Store the analysis under the session: overview, knowledge base and chunks."""
    analysis_dir = settings.session_dir(session_id) / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    write_knowledge_base(analysis_dir, knowledge)
    write_chunks(analysis_dir, chunk_list)
    # Atomic write: a crash mid-write must not leave a truncated repository.json
    # behind (it would turn every later overview read into a 500).
    tmp_path = analysis_dir / "repository.json.tmp"
    tmp_path.write_text(response.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp_path, analysis_dir / "repository.json")


def _cleanup_quietly(session_id: str) -> None:
    try:
        delete_session(session_id)
    except Exception:
        logger.warning("Could not clean up session %s", session_id)


@router.get("/repository/{session_id}/overview", response_model=AnalyzeResponse)
def get_overview(session_id: str) -> AnalyzeResponse:
    """Return the persisted scan overview for an existing session."""
    session_dir = get_session_dir(session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    overview_path = session_dir / "analysis" / "repository.json"
    if not overview_path.is_file():
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
