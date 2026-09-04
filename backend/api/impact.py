"""Impact analysis endpoints (spec §26, §40): static result, optional AI explanation."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.api.deps import knowledge_or_error
from backend.impact.analyzer import ImpactResult, UnknownTargetError, analyze_impact
from backend.impact.explain import ImpactExplanation, explain_impact
from backend.llm.ollama_client import (
    LLMError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ImpactRequest(BaseModel):
    session_id: str
    target: str = Field(..., min_length=1, max_length=500, description="Entity id or file path")
    depth: int = Field(2, ge=1, le=3)


class ImpactExplainRequest(ImpactRequest):
    refresh: bool = False


@router.post("/impact", response_model=ImpactResult)
def impact(request: ImpactRequest) -> ImpactResult:
    """Static callers, importers, subclasses and transitive dependents of a target."""
    _, index = knowledge_or_error(request.session_id)
    try:
        return analyze_impact(index, request.target, depth=request.depth)
    except UnknownTargetError as exc:
        raise HTTPException(status_code=404, detail=f"No entity or file '{exc}' in this analysis.") from exc


@router.post("/impact/explain", response_model=ImpactExplanation)
async def impact_explain(request: ImpactExplainRequest) -> ImpactExplanation:
    """AI reading of the static result: consequences, what to check, tests to run."""
    session_dir, index = knowledge_or_error(request.session_id)
    try:
        result = analyze_impact(index, request.target, depth=request.depth)
    except UnknownTargetError as exc:
        raise HTTPException(status_code=404, detail=f"No entity or file '{exc}' in this analysis.") from exc
    try:
        return await run_in_threadpool(explain_impact, session_dir, index, result, request.refresh)
    except (LLMUnavailableError, LLMModelMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
