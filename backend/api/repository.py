"""Repository read endpoints beyond the overview: entity search for pickers and links."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.api.deps import knowledge_or_error
from backend.knowledge.store import KnowledgeIndex
from backend.parser.models import Entity

router = APIRouter()


class EntitySummary(BaseModel):
    id: str
    type: str
    name: str
    file: str
    start_line: int
    end_line: int
    parent: str | None = None
    signature: str | None = None
    docstring: str | None = None  # first line only
    dependents: int = 0  # incoming calls + imports + inherits


class EntitySearchResponse(BaseModel):
    session_id: str
    query: str
    results: list[EntitySummary] = Field(default_factory=list)
    total_entities: int


@router.get("/repository/{session_id}/entities", response_model=EntitySearchResponse)
def search_entities(
    session_id: str,
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=100),
    types: str | None = Query(None, description="Comma-separated entity types to include"),
) -> EntitySearchResponse:
    """Entities matching a query; with an empty query, the most depended-upon ones."""
    _, index = knowledge_or_error(session_id)
    wanted = {t.strip() for t in types.split(",")} if types else None
    if q.strip():
        matches = [e for e in index.search(q, limit=limit * 3) if wanted is None or e.type in wanted]
    else:
        pool = [e for e in index.entities if wanted is None or e.type in wanted]
        matches = sorted(pool, key=lambda e: (-_dependents(index, e), e.id))
    results = [_summary(index, e) for e in matches[:limit]]
    return EntitySearchResponse(session_id=session_id, query=q, results=results, total_entities=len(index.entities))


def _dependents(index: KnowledgeIndex, entity: Entity) -> int:
    return sum(len(index.incoming[r].get(entity.id, [])) for r in ("calls", "imports", "inherits"))


def _summary(index: KnowledgeIndex, entity: Entity) -> EntitySummary:
    return EntitySummary(
        id=entity.id,
        type=entity.type,
        name=entity.name,
        file=entity.file,
        start_line=entity.start_line,
        end_line=entity.end_line,
        parent=entity.parent,
        signature=entity.signature,
        docstring=entity.docstring.strip().split("\n")[0][:160] if entity.docstring else None,
        dependents=_dependents(index, entity),
    )
