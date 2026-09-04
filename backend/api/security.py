"""Security endpoints: the scanner report, AI explanations and fix suggestions (spec §40)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.analysis.status import load_status
from backend.llm.ollama_client import (
    LLMError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from backend.repository.clone import get_session_dir
from backend.security.engine import load_report
from backend.security.explain import (
    FindingNotFoundError,
    SecurityExplanation,
    SecurityFix,
    explain_finding,
    suggest_fix,
)
from backend.security.models import SecurityReport

logger = logging.getLogger(__name__)
router = APIRouter()


class FindingRequest(BaseModel):
    session_id: str
    finding_id: str = Field(..., min_length=1, max_length=64)
    refresh: bool = False  # ignore the cached AI result and ask the model again


def _report_or_404(session_id: str) -> tuple[SecurityReport, object]:
    session_dir = get_session_dir(session_id)
    if session_dir is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    report = load_report(session_dir)
    if report is None:
        status = load_status(session_id)
        if status is not None and status.state == "running":
            raise HTTPException(status_code=409, detail="Analysis is still running.")
        raise HTTPException(
            status_code=404,
            detail="No security report for this session. Re-analyze the repository to run the scanners.",
        )
    return report, session_dir


@router.get("/security/{session_id}", response_model=SecurityReport)
def get_security_report(session_id: str) -> SecurityReport:
    """Normalized Semgrep + Gitleaks findings (secret values redacted) with scanner statuses."""
    report, _ = _report_or_404(session_id)
    return report


@router.post("/security/explain", response_model=SecurityExplanation)
async def explain(request: FindingRequest) -> SecurityExplanation:
    """AI explanation of one finding: what, why, impact, data flow, remediation."""
    report, session_dir = _report_or_404(request.session_id)
    return await _run_ai(explain_finding, request, report, session_dir)


@router.post("/security/fix", response_model=SecurityFix)
async def fix(request: FindingRequest) -> SecurityFix:
    """AI fix suggestion for one finding as explanation + corrected code + unified diff."""
    report, session_dir = _report_or_404(request.session_id)
    return await _run_ai(suggest_fix, request, report, session_dir)


async def _run_ai(operation, request: FindingRequest, report: SecurityReport, session_dir):
    try:
        return await run_in_threadpool(
            operation, request.session_id, session_dir, report, request.finding_id, request.refresh
        )
    except FindingNotFoundError:
        raise HTTPException(status_code=404, detail=f"Finding '{request.finding_id}' not found in this session.")
    except (LLMUnavailableError, LLMModelMissingError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Could not read session data for %s", request.session_id)
        raise HTTPException(status_code=500, detail="Stored analysis could not be read.") from exc
