"""Architecture graph endpoint (spec §25, §40)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.api.deps import knowledge_or_error
from backend.architecture.graph import DEFAULT_MAX_NODES, ArchitectureGraph, UnknownFocusError, build_graph

router = APIRouter()


@router.get("/architecture/{session_id}", response_model=ArchitectureGraph)
def get_architecture(
    session_id: str,
    focus: str | None = Query(None, max_length=500, description="Entity or file id to center the graph on"),
    depth: int = Query(1, ge=1, le=3, description="Neighbourhood depth around the focus"),
    max_nodes: int = Query(DEFAULT_MAX_NODES, ge=10, le=600),
) -> ArchitectureGraph:
    """File-level dependency graph, or the entity neighbourhood of a focus id."""
    _, index = knowledge_or_error(session_id)
    try:
        return build_graph(index, focus=focus, depth=depth, max_nodes=max_nodes)
    except UnknownFocusError as exc:
        raise HTTPException(status_code=404, detail=f"No entity or file '{exc}' in this analysis.") from exc
