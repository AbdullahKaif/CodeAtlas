"""Repository-aware chat (spec §34) and local-LLM health."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.llm.ollama_client import (
    ChatMessage,
    LLMError,
    LLMHealth,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
    get_llm_client,
)
from backend.rag.embeddings import EmbeddingError
from backend.rag.pipeline import ChatAnswer, answer_question
from backend.rag.vector_store import VectorStoreError
from backend.repository.clone import get_session_dir

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    question: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    top_k: int | None = Field(None, ge=1, le=30)


@router.get("/llm/health", response_model=LLMHealth)
async def llm_health() -> LLMHealth:
    """Whether the local LLM can answer, with setup instructions when it cannot."""
    return await run_in_threadpool(get_llm_client().health_check)


@router.post("/chat", response_model=ChatAnswer)
async def chat(request: ChatRequest) -> ChatAnswer:
    """Answer a question about an analyzed repository, grounded in retrieved code."""
    session_dir = get_session_dir(request.session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    try:
        return await run_in_threadpool(
            answer_question,
            request.session_id,
            session_dir,
            request.question,
            request.history,
            request.top_k,
        )
    except VectorStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LLMUnavailableError, LLMModelMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Could not read analysis data for session %s", request.session_id)
        raise HTTPException(status_code=500, detail="Stored analysis could not be read.") from exc
