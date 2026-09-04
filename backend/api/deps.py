"""Shared request helpers: resolve a session to its knowledge index or a clear HTTP error."""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from backend.analysis.status import load_status
from backend.knowledge.store import KnowledgeIndex, load_knowledge
from backend.repository.clone import get_session_dir


def session_dir_or_404(session_id: str) -> Path:
    session_dir = get_session_dir(session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session_dir


def knowledge_or_error(session_id: str) -> tuple[Path, KnowledgeIndex]:
    """The session's knowledge index; 409 while the analysis runs, 404 when there is none."""
    session_dir = session_dir_or_404(session_id)
    try:
        return session_dir, load_knowledge(session_dir)
    except (OSError, ValueError) as exc:
        status = load_status(session_id)
        if status is not None and status.state == "running":
            raise HTTPException(status_code=409, detail="Analysis is still running.") from exc
        if status is not None and status.state == "failed":
            raise HTTPException(status_code=409, detail=status.error or "Analysis failed.") from exc
        raise HTTPException(status_code=404, detail="No analysis found for this session.") from exc
