"""Developer onboarding endpoints (spec §27, §40)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from backend.api.deps import knowledge_or_error
from backend.llm.ollama_client import (
    LLMError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from backend.onboarding.generator import OnboardingGuide, generate_onboarding
from backend.onboarding.summary import RepositorySummary, summarize_repository
from backend.rag.embeddings import EmbeddingError
from backend.rag.vector_store import VectorStoreError
from backend.security.engine import load_report

logger = logging.getLogger(__name__)
router = APIRouter()


class SummaryRequest(BaseModel):
    session_id: str
    refresh: bool = False


@router.get("/onboarding/{session_id}", response_model=OnboardingGuide)
def get_onboarding(session_id: str) -> OnboardingGuide:
    """Evidence-based onboarding guide: overview, important files, reading order, stages, learning path."""
    session_dir, index = knowledge_or_error(session_id)
    return generate_onboarding(index, load_report(session_dir))


@router.post("/onboarding/summary", response_model=RepositorySummary)
async def onboarding_summary(request: SummaryRequest) -> RepositorySummary:
    """AI repository summary grounded in retrieved documentation and code (cached per session)."""
    session_dir, _ = knowledge_or_error(request.session_id)
    try:
        return await run_in_threadpool(summarize_repository, request.session_id, session_dir, request.refresh)
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
