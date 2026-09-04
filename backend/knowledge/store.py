"""Read-side view of a session's knowledge base for graph, impact and onboarding.

Loads entities, relationships and the scan once per session state (cached by
the mtime of entities.json) and exposes the indexes every consumer needs:
entities by id and by file, adjacency in both directions per relation, file
metadata, packages. The knowledge base on disk stays the source of truth;
this is a derived, in-memory convenience.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path, PurePosixPath

from backend.knowledge.serializer import ENTITIES_FILE, load_entities, load_relationships
from backend.parser.models import Entity, Relationship
from backend.repository.scanner import FileInfo

_CACHE_SIZE = 4


class KnowledgeIndex:
    def __init__(
        self,
        entities: list[Entity],
        relationships: list[Relationship],
        files: list[FileInfo],
        repo_name: str,
    ) -> None:
        self.repo_name = repo_name
        self.entities = entities
        self.relationships = relationships
        self.by_id: dict[str, Entity] = {e.id: e for e in entities}
        self.by_file: dict[str, list[Entity]] = defaultdict(list)
        for entity in entities:
            if entity.type != "file":
                self.by_file[entity.file].append(entity)
        self.files: dict[str, FileInfo] = {f.path: f for f in files}
        # Adjacency per relation: outgoing[relation][source] -> [edges], incoming[relation][target] -> [edges]
        self.outgoing: dict[str, dict[str, list[Relationship]]] = defaultdict(lambda: defaultdict(list))
        self.incoming: dict[str, dict[str, list[Relationship]]] = defaultdict(lambda: defaultdict(list))
        for edge in relationships:
            self.outgoing[edge.relation][edge.source].append(edge)
            self.incoming[edge.relation][edge.target].append(edge)

    # -- helpers ---------------------------------------------------------------

    def entity(self, entity_id: str) -> Entity | None:
        return self.by_id.get(entity_id)

    def file_of(self, entity_id: str) -> str:
        entity = self.by_id.get(entity_id)
        return entity.file if entity is not None else entity_id.split("::", 1)[0]

    def is_test(self, path: str) -> bool:
        info = self.files.get(path)
        if info is not None:
            return info.is_test_file
        parts = PurePosixPath(path).parts
        name = parts[-1].lower() if parts else ""
        return name.startswith("test_") or any(p.lower() in {"test", "tests", "__tests__"} for p in parts[:-1])

    def is_entry_point(self, path: str) -> bool:
        info = self.files.get(path)
        return bool(info and info.is_entry_point)

    def package_of(self, path: str) -> str:
        parts = PurePosixPath(path).parts
        return parts[0] if len(parts) > 1 else "(root)"

    def source_files(self) -> list[str]:
        """Files that hold parsed code entities (the nodes of a file-level graph)."""
        return sorted(path for path, entities in self.by_file.items() if entities)

    def members(self, entity_id: str) -> list[Entity]:
        """The entity plus everything it contains (a class's methods, a file's definitions)."""
        result: list[Entity] = []
        root = self.by_id.get(entity_id)
        if root is not None:
            result.append(root)
        stack = [entity_id]
        seen = {entity_id}
        while stack:
            current = stack.pop()
            for edge in self.outgoing["contains"].get(current, []):
                if edge.target in seen:
                    continue
                seen.add(edge.target)
                child = self.by_id.get(edge.target)
                if child is not None:
                    result.append(child)
                    stack.append(edge.target)
        return result

    def search(self, query: str, limit: int = 20) -> list[Entity]:
        """Case-insensitive substring match on id/name; shorter, earlier matches first."""
        needle = query.strip().lower()
        if not needle:
            return []
        scored: list[tuple[int, int, str, Entity]] = []
        for entity in self.entities:
            haystack = entity.id.lower()
            name = entity.name.lower()
            if name == needle:
                rank = 0
            elif name.startswith(needle):
                rank = 1
            elif needle in name:
                rank = 2
            elif needle in haystack:
                rank = 3
            else:
                continue
            scored.append((rank, len(entity.id), entity.id, entity))
        scored.sort()
        return [entity for _, _, _, entity in scored[:limit]]


_cache: dict[str, tuple[float, KnowledgeIndex]] = {}
_cache_lock = threading.Lock()


def load_knowledge(session_dir: Path) -> KnowledgeIndex:
    """The session's knowledge index; reloaded when entities.json changes."""
    analysis_dir = session_dir / "analysis"
    key = str(session_dir)
    mtime = (analysis_dir / ENTITIES_FILE).stat().st_mtime  # OSError propagates: no analysis
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    entities = load_entities(analysis_dir)
    relationships = load_relationships(analysis_dir)
    files: list[FileInfo] = []
    repo_name = session_dir.name
    try:
        overview = json.loads((analysis_dir / "repository.json").read_text(encoding="utf-8"))
        files = [FileInfo.model_validate(f) for f in overview.get("scan", {}).get("files", [])]
        repo_name = overview.get("repository", {}).get("name", repo_name)
    except (OSError, ValueError):
        pass
    index = KnowledgeIndex(entities, relationships, files, repo_name)

    with _cache_lock:
        if len(_cache) >= _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (mtime, index)
    return index
