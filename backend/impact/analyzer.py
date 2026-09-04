"""Static impact analysis (spec §26): who depends on a file, class, function or method.

Everything here is derived from the knowledge base's edges - callers via
``calls``, importers via ``imports``, subclasses via ``inherits`` - followed
transitively up to a small depth. It is deliberately labelled static: dynamic
dispatch, reflection and runtime configuration are invisible to it, so the
result is a lower bound on what may be affected, never a guarantee.
"""
from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, Field

from backend.knowledge.store import KnowledgeIndex
from backend.parser.models import Entity

ImpactLevel = Literal["HIGH", "MEDIUM", "LOW"]
Via = Literal["calls", "imports", "inherits", "member"]
MAX_DEPTH = 3


class ImpactTarget(BaseModel):
    id: str
    type: str
    name: str
    file: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    members: int = 0  # contained entities considered part of the change (a class's methods, ...)


class AffectedEntity(BaseModel):
    id: str
    type: str
    name: str
    file: str
    start_line: int
    via: Via  # how it depends on the previous hop
    through: str  # the entity id it depends on (target or an earlier hop)
    depth: int  # 1 = direct dependent
    line: int | None = None  # where the dependency is expressed (call/import site)
    is_test: bool = False


class ImpactCounts(BaseModel):
    callers: int
    importers: int
    subclasses: int
    transitive: int
    files: int
    tests: int


class ImpactResult(BaseModel):
    target: ImpactTarget
    level: ImpactLevel
    reasons: list[str]
    affected: list[AffectedEntity]
    files: list[str]
    tests: list[str]
    counts: ImpactCounts
    depth: int
    truncated: bool = False
    note: str = (
        "Static impact analysis: derived from resolved imports, calls and inheritance in the "
        "knowledge base. Dynamic dispatch, reflection and configuration-driven wiring are not "
        "visible to it, so this is a lower bound on what may be affected."
    )


class UnknownTargetError(Exception):
    pass


def analyze_impact(index: KnowledgeIndex, target_id: str, depth: int = 2, max_affected: int = 500) -> ImpactResult:
    target = index.entity(target_id)
    if target is None:
        raise UnknownTargetError(target_id)
    depth = max(1, min(depth, MAX_DEPTH))

    members = index.members(target_id)
    member_ids = {m.id for m in members}
    affected: dict[str, AffectedEntity] = {}
    queue: deque[tuple[str, int]] = deque((m.id, 0) for m in members)
    truncated = False

    while queue:
        current, level = queue.popleft()
        if level >= depth:
            continue
        for via, edges in (
            ("calls", index.incoming["calls"].get(current, [])),
            ("inherits", index.incoming["inherits"].get(current, [])),
            ("imports", index.incoming["imports"].get(_file_or_self(index, current), []) if level == 0 or via_file(index, current) else []),
        ):
            for edge in edges:
                dependent_id = edge.source
                if dependent_id in member_ids or dependent_id in affected:
                    continue
                entity = index.entity(dependent_id)
                if entity is None:
                    continue
                if len(affected) >= max_affected:
                    truncated = True
                    queue.clear()
                    break
                affected[dependent_id] = AffectedEntity(
                    id=entity.id,
                    type=entity.type,
                    name=entity.name,
                    file=entity.file,
                    start_line=entity.start_line,
                    via=via,  # type: ignore[arg-type]
                    through=current if via != "imports" else _file_or_self(index, current),
                    depth=level + 1,
                    line=edge.line,
                    is_test=index.is_test(entity.file),
                )
                queue.append((dependent_id, level + 1))

    ordered = sorted(affected.values(), key=lambda a: (a.depth, a.is_test, a.file, a.start_line))
    files = sorted({a.file for a in ordered if a.file != target.file} | {a.file for a in ordered if a.type == "file"})
    tests = sorted({a.file for a in ordered if a.is_test})
    counts = ImpactCounts(
        callers=sum(1 for a in ordered if a.depth == 1 and a.via == "calls"),
        importers=sum(1 for a in ordered if a.depth == 1 and a.via == "imports"),
        subclasses=sum(1 for a in ordered if a.depth == 1 and a.via == "inherits"),
        transitive=sum(1 for a in ordered if a.depth > 1),
        files=len(files),
        tests=len(tests),
    )
    level_value, reasons = _assess(target, members, ordered, counts, index)
    return ImpactResult(
        target=ImpactTarget(
            id=target.id,
            type=target.type,
            name=target.name,
            file=target.file,
            start_line=target.start_line,
            end_line=target.end_line,
            signature=target.signature,
            docstring=target.docstring,
            members=len(members) - 1,
        ),
        level=level_value,
        reasons=reasons,
        affected=ordered,
        files=files,
        tests=tests,
        counts=counts,
        depth=depth,
        truncated=truncated,
    )


def via_file(index: KnowledgeIndex, entity_id: str) -> bool:
    """Only files have importers; imports of an entity's file are counted at depth 0 only."""
    entity = index.entity(entity_id)
    return entity is not None and entity.type == "file"


def _file_or_self(index: KnowledgeIndex, entity_id: str) -> str:
    entity = index.entity(entity_id)
    return entity.file if entity is not None else entity_id


def _assess(
    target: Entity, members: list[Entity], affected: list[AffectedEntity], counts: ImpactCounts, index: KnowledgeIndex
) -> tuple[ImpactLevel, list[str]]:
    direct = counts.callers + counts.importers + counts.subclasses
    non_test_direct = sum(1 for a in affected if a.depth == 1 and not a.is_test)
    reasons: list[str] = []
    if counts.callers:
        reasons.append(f"{counts.callers} direct caller{'s' if counts.callers != 1 else ''}")
    if counts.importers:
        reasons.append(f"imported by {counts.importers} file{'s' if counts.importers != 1 else ''}")
    if counts.subclasses:
        reasons.append(f"{counts.subclasses} subclass{'es' if counts.subclasses != 1 else ''} inherit from it")
    if counts.transitive:
        reasons.append(f"{counts.transitive} transitive dependent{'s' if counts.transitive != 1 else ''}")
    if counts.tests:
        reasons.append(f"covered by {counts.tests} test file{'s' if counts.tests != 1 else ''}")
    else:
        reasons.append("no test file references it")
    if target.type == "class" and len(members) > 1:
        reasons.append(f"changing the class touches its {len(members) - 1} member{'s' if len(members) != 2 else ''}")
    if index.is_entry_point(target.file):
        reasons.append("lives in an entry point")

    if non_test_direct >= 4 or counts.files >= 4 or counts.subclasses >= 2:
        return "HIGH", reasons
    if direct >= 1 or counts.transitive >= 1:
        return "MEDIUM", reasons
    reasons.append("no static dependents found")
    return "LOW", reasons
