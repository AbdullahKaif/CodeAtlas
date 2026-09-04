"""Analysis status: the real, persisted progress of a background analysis.

Progress is never faked (spec §41): a stage is only marked running/completed
when the pipeline actually enters/leaves it, and embedding reports true chunk
counts. status.json lives at the session root - NOT under analysis/ - so it
survives the privacy scrub that removes repository content when a run fails.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger(__name__)

STATUS_FILE = "status.json"

# Pipeline order; the frontend renders these in sequence.
STAGES = ["cloning", "scanning", "parsing", "chunking", "embedding", "indexing", "security"]

StageState = Literal["pending", "running", "completed", "failed"]


class StageStatus(BaseModel):
    name: str
    state: StageState = "pending"
    detail: str | None = None  # e.g. "1200/4000 chunks"


class AnalysisStatus(BaseModel):
    session_id: str
    state: Literal["running", "completed", "failed"]
    stages: list[StageStatus]
    error: str | None = None
    started_at: str
    finished_at: str | None = None
    repository: dict[str, str] = Field(default_factory=dict)  # name/url, for progress UIs


class StatusTracker:
    """Owns one session's status.json; every mutation is persisted atomically."""

    def __init__(self, session_id: str, repo_name: str, repo_url: str) -> None:
        self.status = AnalysisStatus(
            session_id=session_id,
            state="running",
            stages=[StageStatus(name=name) for name in STAGES],
            started_at=_now(),
            repository={"name": repo_name, "url": repo_url},
        )
        self._path = settings.session_dir(session_id) / STATUS_FILE
        self._write()

    def start(self, stage: str, detail: str | None = None) -> None:
        self._stage(stage).state = "running"
        self._stage(stage).detail = detail
        self._write()

    def progress(self, stage: str, detail: str) -> None:
        self._stage(stage).detail = detail
        self._write()

    def complete(self, stage: str, detail: str | None = None) -> None:
        entry = self._stage(stage)
        entry.state = "completed"
        if detail is not None:
            entry.detail = detail
        self._write()

    def stage_failed(self, stage: str, detail: str) -> None:
        """An optional stage failed; the analysis itself continues."""
        entry = self._stage(stage)
        entry.state = "failed"
        entry.detail = detail
        self._write()

    def finish(self) -> None:
        self.status.state = "completed"
        self.status.finished_at = _now()
        self._write()

    def fail(self, error: str) -> None:
        """The analysis is over: the running stage is marked failed, the rest stay pending."""
        self.status.state = "failed"
        self.status.error = error
        self.status.finished_at = _now()
        for entry in self.status.stages:
            if entry.state == "running":
                entry.state = "failed"
        self._write()

    def _stage(self, name: str) -> StageStatus:
        return next(entry for entry in self.status.stages if entry.name == name)

    def _write(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".json.tmp")
            tmp_path.write_text(self.status.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp_path, self._path)
        except OSError:
            # Status is observability, not the analysis itself: losing one
            # update must not kill the pipeline (e.g. session deleted mid-run).
            logger.warning("Could not write status for session %s", self.status.session_id)


def load_status(session_id: str) -> AnalysisStatus | None:
    path = settings.session_dir(session_id) / STATUS_FILE
    try:
        return AnalysisStatus.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
