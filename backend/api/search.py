"""Retrieval endpoint: top-k chunks for a question, grounded in one session."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.rag.embeddings import EmbeddingError
from backend.rag.models import RetrievedChunk
from backend.rag.retriever import retrieve
from backend.rag.vector_store import VectorStoreError
from backend.repository.clone import get_session_dir

logger = logging.getLogger(__name__)
router = APIRouter()


class SearchRequest(BaseModel):
    session_id: str
    question: str = Field(..., min_length=1, max_length=2000)
    top_k: int | None = Field(None, ge=1, le=50)


class SearchResponse(BaseModel):
    session_id: str
    question: str
    results: list[RetrievedChunk]


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Embed the question locally and return the most similar chunks."""
    session_dir = get_session_dir(request.session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    try:
        results = await run_in_threadpool(retrieve, session_dir, request.question, request.top_k)
    except VectorStoreError as exc:
        # The session exists but has no usable index (embedding failed or is
        # from another model): a conflict with the session's state, not a 404.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Could not read analysis data for session %s", request.session_id)
        raise HTTPException(status_code=500, detail="Stored analysis could not be read.") from exc
    return SearchResponse(session_id=request.session_id, question=request.question, results=results)
