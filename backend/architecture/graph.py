"""Architecture graph for React Flow (spec §25).

Two views over the same knowledge base:
- file level (default): one node per source file, edges aggregate the
  imports / calls / inherits relationships between the entities of two files;
- focus view: one node per entity around a chosen file or entity, with the
  raw relationship edges, up to a configurable depth.

Large repositories are cut down deterministically (highest-degree nodes
first) and the response says so, so the browser never has to draw thousands
of nodes at once.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from backend.knowledge.store import KnowledgeIndex

GraphLevel = Literal["file", "entity"]
EDGE_RELATIONS = ("imports", "calls", "inherits")
DEFAULT_MAX_NODES = 150


class GraphNode(BaseModel):
    id: str
    label: str
    type: Literal["file", "class", "function", "method"]
    file: str
    package: str
    language: str | None = None
    is_test: bool = False
    is_entry_point: bool = False
    start_line: int | None = None
    end_line: int | None = None
    docstring: str | None = None
    classes: int = 0  # file nodes: contained classes
    functions: int = 0  # file nodes: functions + methods
    degree: int = 0  # edges touching this node in the full graph (not just the shown part)


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: Literal["imports", "calls", "inherits", "contains"]
    count: int = 1  # aggregated relationships behind this edge (file level)


class GraphStats(BaseModel):
    level: GraphLevel
    total_nodes: int
    total_edges: int
    shown_nodes: int
    shown_edges: int
    truncated: bool
    focus: str | None = None
    depth: int | None = None


class ArchitectureGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    packages: list[str] = Field(default_factory=list)
    stats: GraphStats
    note: str = (
        "Static structure extracted from source: imports, resolved calls and inheritance. "
        "Dynamic dispatch and runtime wiring are not represented."
    )


class UnknownFocusError(Exception):
    pass


def build_graph(
    index: KnowledgeIndex,
    focus: str | None = None,
    depth: int = 1,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> ArchitectureGraph:
    if focus:
        return _focus_graph(index, focus, depth, max_nodes)
    return _file_graph(index, max_nodes)


# ---------------------------------------------------------------------------
# File level
# ---------------------------------------------------------------------------

def _file_graph(index: KnowledgeIndex, max_nodes: int) -> ArchitectureGraph:
    files = index.source_files()
    aggregated: dict[tuple[str, str, str], int] = defaultdict(int)
    for relation in EDGE_RELATIONS:
        for edge in index.relationships:
            if edge.relation != relation:
                continue
            source_file, target_file = index.file_of(edge.source), index.file_of(edge.target)
            if source_file == target_file or source_file not in index.by_file or target_file not in index.by_file:
                continue
            aggregated[(source_file, target_file, relation)] += 1

    degree: dict[str, int] = defaultdict(int)
    for (source, target, _), count in aggregated.items():
        degree[source] += count
        degree[target] += count

    total_nodes = len(files)
    # Keep the most connected files; ties broken by path so the cut is stable.
    kept = sorted(files, key=lambda f: (-degree[f], f))[:max_nodes]
    kept_set = set(kept)
    nodes = [_file_node(index, path, degree[path]) for path in sorted(kept)]
    edges = [
        GraphEdge(id=f"{s}->{t}:{r}", source=s, target=t, relation=r, count=n)
        for (s, t, r), n in sorted(aggregated.items())
        if s in kept_set and t in kept_set
    ]
    return ArchitectureGraph(
        nodes=nodes,
        edges=edges,
        packages=sorted({n.package for n in nodes}),
        stats=GraphStats(
            level="file",
            total_nodes=total_nodes,
            total_edges=len(aggregated),
            shown_nodes=len(nodes),
            shown_edges=len(edges),
            truncated=len(nodes) < total_nodes,
        ),
    )


def _file_node(index: KnowledgeIndex, path: str, degree: int) -> GraphNode:
    entities = index.by_file.get(path, [])
    file_entity = index.entity(path)
    info = index.files.get(path)
    return GraphNode(
        id=path,
        label=path.rsplit("/", 1)[-1],
        type="file",
        file=path,
        package=index.package_of(path),
        language=info.language if info else (file_entity.language if file_entity else None),
        is_test=index.is_test(path),
        is_entry_point=index.is_entry_point(path),
        start_line=1,
        end_line=file_entity.end_line if file_entity else None,
        docstring=file_entity.docstring if file_entity else None,
        classes=sum(1 for e in entities if e.type == "class"),
        functions=sum(1 for e in entities if e.type in {"function", "method"}),
        degree=degree,
    )


# ---------------------------------------------------------------------------
# Focus (entity level)
# ---------------------------------------------------------------------------

def _focus_graph(index: KnowledgeIndex, focus: str, depth: int, max_nodes: int) -> ArchitectureGraph:
    if focus not in index.by_id:
        raise UnknownFocusError(focus)
    depth = max(1, min(depth, 3))
    # Start from the focus entity and everything it contains (a file's definitions,
    # a class's methods), then expand along the relationship edges.
    seeds = {e.id for e in index.members(focus)}
    visited: dict[str, int] = {seed: 0 for seed in seeds}
    frontier = list(seeds)
    for level in range(1, depth + 1):
        next_frontier: list[str] = []
        for node_id in frontier:
            for relation in EDGE_RELATIONS:
                for edge in index.outgoing[relation].get(node_id, []):
                    if edge.target not in visited:
                        visited[edge.target] = level
                        next_frontier.append(edge.target)
                for edge in index.incoming[relation].get(node_id, []):
                    if edge.source not in visited:
                        visited[edge.source] = level
                        next_frontier.append(edge.source)
        frontier = next_frontier
        if len(visited) > max_nodes * 3:
            break

    total = len(visited)
    degree = {n: sum(len(index.outgoing[r].get(n, [])) + len(index.incoming[r].get(n, [])) for r in EDGE_RELATIONS) for n in visited}
    # Nearest first, then most connected: the focus and its own members always survive the cut.
    kept = sorted(visited, key=lambda n: (visited[n], -degree[n], n))[:max_nodes]
    kept_set = set(kept)
    nodes = [_entity_node(index, n, degree[n]) for n in kept]
    edges: list[GraphEdge] = []
    seen: set[str] = set()
    for relation in EDGE_RELATIONS + ("contains",):
        for edge in index.relationships:
            if edge.relation != relation or edge.source not in kept_set or edge.target not in kept_set:
                continue
            edge_id = f"{edge.source}->{edge.target}:{relation}"
            if edge_id in seen:
                continue
            seen.add(edge_id)
            edges.append(GraphEdge(id=edge_id, source=edge.source, target=edge.target, relation=relation))
    return ArchitectureGraph(
        nodes=nodes,
        edges=edges,
        packages=sorted({n.package for n in nodes}),
        stats=GraphStats(
            level="entity",
            total_nodes=total,
            total_edges=len(edges),
            shown_nodes=len(nodes),
            shown_edges=len(edges),
            truncated=len(nodes) < total,
            focus=focus,
            depth=depth,
        ),
    )


def _entity_node(index: KnowledgeIndex, entity_id: str, degree: int) -> GraphNode:
    entity = index.by_id[entity_id]
    if entity.type == "file":
        node = _file_node(index, entity.file, degree)
        return node
    return GraphNode(
        id=entity.id,
        label=entity.id.split("::", 1)[1] if "::" in entity.id else entity.name,
        type=entity.type,
        file=entity.file,
        package=index.package_of(entity.file),
        language=entity.language,
        is_test=index.is_test(entity.file),
        start_line=entity.start_line,
        end_line=entity.end_line,
        docstring=entity.docstring,
        degree=degree,
    )
