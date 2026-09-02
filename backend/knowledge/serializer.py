"""Persist and load the knowledge base under a session's analysis directory.

Writes are atomic (write to .tmp, then os.replace): a crash mid-write must not
leave truncated JSON behind, because every later phase reads these files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from backend.knowledge.builder import KnowledgeBase
from backend.parser.models import Entity, Relationship

ENTITIES_FILE = "entities.json"
RELATIONSHIPS_FILE = "relationships.json"


def write_knowledge_base(analysis_dir: Path, knowledge: KnowledgeBase) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(
        analysis_dir / ENTITIES_FILE,
        {"entities": [e.model_dump(mode="json") for e in knowledge.entities]},
    )
    _write_atomic(
        analysis_dir / RELATIONSHIPS_FILE,
        {"relationships": [r.model_dump(mode="json") for r in knowledge.relationships]},
    )


def load_entities(analysis_dir: Path) -> list[Entity]:
    data = json.loads((analysis_dir / ENTITIES_FILE).read_text(encoding="utf-8"))
    return [Entity.model_validate(e) for e in data["entities"]]


def load_relationships(analysis_dir: Path) -> list[Relationship]:
    data = json.loads((analysis_dir / RELATIONSHIPS_FILE).read_text(encoding="utf-8"))
    return [Relationship.model_validate(r) for r in data["relationships"]]


def _write_atomic(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
