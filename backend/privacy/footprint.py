"""What a session stores on disk (spec §38): the artifacts deletion removes, with sizes.

The footprint is computed by walking the session directory, so it reports
what is actually there rather than what the pipeline intended to write. It
lists paths and byte counts only; repository content is never read.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

_DESCRIPTIONS = {
    "repository": "The cloned repository (source code)",
    "analysis": "Knowledge base: entities, relationships, chunks, overview and cached AI answers",
    "vectors": "Local embeddings and the FAISS index",
    "security": "Scanner results with secrets redacted",
    "status.json": "Analysis progress record",
}


class Artifact(BaseModel):
    name: str  # top-level entry inside the session directory
    description: str
    bytes: int
    files: int


class SessionFootprint(BaseModel):
    session_id: str
    location: str  # absolute path of the session directory (local machine only)
    total_bytes: int
    total_files: int
    artifacts: list[Artifact] = Field(default_factory=list)
    note: str = (
        "Everything listed lives in this one directory on this machine; deleting the session "
        "removes it entirely. Nothing is stored anywhere else."
    )


def session_footprint(session_id: str, session_dir: Path) -> SessionFootprint:
    artifacts: list[Artifact] = []
    for entry in sorted(session_dir.iterdir(), key=lambda p: p.name):
        size, count = _measure(entry)
        artifacts.append(
            Artifact(
                name=entry.name,
                description=_DESCRIPTIONS.get(entry.name, "Other session data"),
                bytes=size,
                files=count,
            )
        )
    return SessionFootprint(
        session_id=session_id,
        location=str(session_dir),
        total_bytes=sum(a.bytes for a in artifacts),
        total_files=sum(a.files for a in artifacts),
        artifacts=artifacts,
    )


def _measure(path: Path) -> tuple[int, int]:
    try:
        if path.is_file():
            return path.stat().st_size, 1
        size = 0
        count = 0
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    size += child.stat().st_size
                    count += 1
                except OSError:
                    continue
        return size, count
    except OSError:
        return 0, 0
