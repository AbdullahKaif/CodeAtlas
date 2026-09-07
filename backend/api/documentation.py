"""Documentation and test generation endpoints (spec §28, §29, §40).

Both features return suggestions only: Markdown drafts and test modules that
the developer copies or downloads. Nothing is written to the cloned repository.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.api.deps import knowledge_or_error
from backend.documentation.generator import (
    KINDS,
    DocumentationKind,
    DocumentationStatus,
    GeneratedDocumentation,
    UnknownKindError,
    documentation_status,
    generate_documentation,
)
from backend.llm.ollama_client import (
    LLMError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from backend.rag.embeddings import EmbeddingError
from backend.rag.vector_store import VectorStoreError
from backend.testgen.generator import (
    TestingConventions,
    TestSuggestion,
    UnknownTargetError,
    UnsupportedTargetError,
    detect_conventions,
    generate_tests,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentationRequest(BaseModel):
    session_id: str
    kind: DocumentationKind
    refresh: bool = False


class TestsRequest(BaseModel):
    session_id: str
    target: str = Field(..., min_length=1, max_length=500, description="Entity id of a function or method")
    refresh: bool = False


def _raise_for_llm(exc: Exception) -> None:
    if isinstance(exc, VectorStoreError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, EmbeddingError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, (LLMUnavailableError, LLMModelMissingError)):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, LLMTimeoutError):
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    if isinstance(exc, LLMError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


@router.get("/documentation/{session_id}", response_model=DocumentationStatus)
def get_documentation_status(session_id: str) -> DocumentationStatus:
    """The document kinds that can be drafted, which are cached, and the documentation the repository already has."""
    session_dir, index = knowledge_or_error(session_id)
    return documentation_status(session_id, session_dir, index)


@router.post("/documentation", response_model=GeneratedDocumentation)
async def post_documentation(request: DocumentationRequest) -> GeneratedDocumentation:
    """Draft one document (README, architecture, developer guide or API overview) as Markdown, cached per session."""
    session_dir, index = knowledge_or_error(request.session_id)
    if request.kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"Unknown documentation kind '{request.kind}'.")
    try:
        return await run_in_threadpool(
            generate_documentation, request.session_id, session_dir, index, request.kind, request.refresh
        )
    except UnknownKindError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown documentation kind '{exc}'.") from exc
    except Exception as exc:  # noqa: BLE001 - mapped to status codes below
        _raise_for_llm(exc)
        raise


@router.get("/tests/{session_id}/conventions", response_model=TestingConventions)
def get_testing_conventions(session_id: str) -> TestingConventions:
    """The project's testing conventions as detected from its files (framework, layout, naming)."""
    _, index = knowledge_or_error(session_id)
    return detect_conventions(index)


@router.post("/tests", response_model=TestSuggestion)
async def post_tests(request: TestsRequest) -> TestSuggestion:
    """Suggested tests for one function or method: normal, edge, invalid-input and error cases. Never written to the repository."""
    session_dir, index = knowledge_or_error(request.session_id)
    try:
        return await run_in_threadpool(
            generate_tests, request.session_id, session_dir, index, request.target, request.refresh
        )
    except UnknownTargetError as exc:
        raise HTTPException(status_code=404, detail=f"No entity '{exc}' in this analysis.") from exc
    except UnsupportedTargetError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Tests can be suggested for functions and methods only; '{request.target}' is a {exc}.",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - mapped to status codes below
        _raise_for_llm(exc)
        raise
