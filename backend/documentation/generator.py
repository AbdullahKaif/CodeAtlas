"""Documentation drafts from repository evidence (spec §28).

Four kinds of document can be drafted: a README, architecture documentation, a
developer guide and an API overview. Each draft follows the RAG contract: the
local model sees a deterministic fact sheet computed from the knowledge base
(packages, entry points, dependency files, existing documentation) plus a
bounded set of retrieved chunks, never the whole repository, and every citation
it makes is validated. Drafts are returned as Markdown and cached inside the
session. Nothing is ever written into the cloned repository, and existing
documentation is reported so the reader can see what a draft would sit next to.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from backend.impact.explain import entity_chunk
from backend.knowledge.store import KnowledgeIndex
from backend.llm.ollama_client import LLMClient, get_llm_client
from backend.onboarding.generator import generate_onboarding
from backend.rag.embeddings import EmbeddingError
from backend.rag.models import RetrievedChunk
from backend.rag.prompts import build_context
from backend.rag.retriever import retrieve
from backend.rag.sources import SourceReference, validate_answer
from backend.rag.vector_store import VectorStoreError

logger = logging.getLogger(__name__)

DocumentationKind = Literal["readme", "architecture", "developer_guide", "api_overview"]

NOTE = (
    "AI-drafted from the repository's own structure and retrieved code. Nothing was written "
    "to the repository; existing documentation is never overwritten. Review before publishing."
)

_RETRIEVAL_TOP_K = 6
_ENTITY_CHUNK_LINES = 60
_MAX_ENTITY_CHUNKS = 6
_DEPENDENCY_FILES = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "pipfile", "poetry.lock", "package.json", "go.mod", "cargo.toml", "pom.xml", "build.gradle",
    "gemfile", "composer.json", "environment.yml", "dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "makefile", "tox.ini", "noxfile.py",
}
_DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
_DOC_DIRS = {"docs", "doc", "documentation", "wiki"}
_ROOT_DOC_STEMS = {"readme", "contributing", "changelog", "changes", "history", "authors", "security", "code_of_conduct", "architecture", "design"}
_ROUTE_DECORATOR_WORDS = ("route", "get", "post", "put", "patch", "delete", "websocket", "api", "router", "command", "endpoint")


class KindSpec(BaseModel):
    kind: DocumentationKind
    title: str
    description: str
    filename: str
    queries: list[str]
    headings: list[str]
    guidance: str


KINDS: dict[str, KindSpec] = {
    "readme": KindSpec(
        kind="readme",
        title="README draft",
        description="What the project is, how it is organised, how to install and run it.",
        filename="README.md",
        queries=[
            "what this project does and its purpose",
            "installation, setup and how to run the application",
            "main entry point and command line usage",
            "configuration and environment variables",
        ],
        headings=["Overview", "Features", "Project structure", "Installation", "Usage", "Configuration", "Testing"],
        guidance=(
            "Write the README a maintainer would publish. Under Features list only capabilities the "
            "excerpts show. Under Installation and Usage give only commands the fact sheet or excerpts "
            "support (a requirements.txt justifies `pip install -r requirements.txt`; an entry point "
            "justifies `python <entry point>`). Where the evidence does not show something, write a "
            "one-line `TODO:` item for the maintainer instead of inventing it."
        ),
    ),
    "architecture": KindSpec(
        kind="architecture",
        title="Architecture documentation",
        description="Components, how they depend on each other, and the main data and control flow.",
        filename="ARCHITECTURE.md",
        queries=[
            "main components and modules of the system",
            "how modules import and call each other",
            "core classes and their responsibilities",
            "application startup and request or data flow",
        ],
        headings=["Purpose", "Components", "Dependencies between components", "Key classes and functions", "Data and control flow", "Extension points"],
        guidance=(
            "Describe the architecture as the code shows it: name packages, files, classes and "
            "functions from the fact sheet and excerpts, and describe dependencies using the import "
            "and call relationships listed. Do not describe layers or services the evidence does not "
            "show. Static structure is not runtime behaviour: say what the code appears to do."
        ),
    ),
    "developer_guide": KindSpec(
        kind="developer_guide",
        title="Developer guide",
        description="How to set up a development environment, navigate the code, test and contribute.",
        filename="DEVELOPER_GUIDE.md",
        queries=[
            "development setup, dependencies and tooling",
            "how tests are written and run",
            "configuration files and environment variables",
            "where to start reading the code and important modules",
        ],
        headings=["Getting started", "Repository layout", "Where to start reading", "Running tests", "Configuration", "Conventions", "Common tasks"],
        guidance=(
            "Write for a developer joining the project. Base the layout on the fact sheet's packages "
            "and important files, the testing section on the test files and framework evidence "
            "listed, and conventions only on patterns visible in the excerpts. Mark anything the "
            "evidence does not settle as a `TODO:` for the maintainer."
        ),
    ),
    "api_overview": KindSpec(
        kind="api_overview",
        title="API overview",
        description="The public surface: endpoints, commands or public functions and classes, with parameters.",
        filename="API.md",
        queries=[
            "HTTP endpoints, routes and request handlers",
            "public functions and classes exported by the package",
            "command line commands and their arguments",
            "request and response models and parameters",
        ],
        headings=["Scope", "Endpoints and commands", "Public classes and functions", "Parameters and return values", "Errors"],
        guidance=(
            "Document the public surface visible in the excerpts. For each endpoint, command, class "
            "or function give its name, location, parameters (from the signatures shown) and what it "
            "appears to do. If the excerpts show no HTTP routes or commands, say so under Scope and "
            "document the public functions and classes instead. Never invent parameters or "
            "return types the signatures do not show."
        ),
    ),
}

SYSTEM_PROMPT = """You are CodeAtlas's documentation writer. Draft ONE Markdown document for ONE repository using ONLY the evidence provided: a fact sheet computed from the repository's structure and numbered code excerpts.

Rules:
1. Every file, package, class, function, command and configuration value you mention must appear in the fact sheet or the excerpts. Never invent names, commands, versions, licences, badges or behaviour.
2. When the evidence does not cover something a reader would expect, write a short `TODO:` line for the maintainer rather than guessing.
3. Static analysis is not runtime truth: describe what the code appears to do.
4. Write in clear, neutral prose. Use the headings you are given, as `##` sections, in the order given; add `###` subsections when useful. Start with a `#` title.
5. Do not put citations inside the document body. End with a "Sources:" section listing every excerpt you relied on, one per line, exactly as:
   - <file path>: lines <start>-<end>
"""


class ExistingDocument(BaseModel):
    path: str
    role: str  # readme | docs | contributing | changelog | other
    size_bytes: int


class DocumentationKindInfo(BaseModel):
    kind: DocumentationKind
    title: str
    description: str
    filename: str
    cached: bool = False


class DocumentationStatus(BaseModel):
    session_id: str
    repository: str
    kinds: list[DocumentationKindInfo]
    existing_documents: list[ExistingDocument] = Field(default_factory=list)
    note: str = NOTE


class GeneratedDocumentation(BaseModel):
    session_id: str
    kind: DocumentationKind
    title: str
    filename: str  # suggested file name; never written by CodeAtlas
    markdown: str
    existing_documents: list[ExistingDocument] = Field(default_factory=list)
    retrieval_used: bool  # False when no vector index was available (fact sheet + entities only)
    sources: list[SourceReference] = Field(default_factory=list)
    context: list[RetrievedChunk] = Field(default_factory=list)
    references_removed: int = 0
    model: str
    cached: bool = False
    generated_at: str
    duration_seconds: float
    note: str = NOTE


class UnknownKindError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def documentation_status(session_id: str, session_dir: Path, index: KnowledgeIndex) -> DocumentationStatus:
    kinds = [
        DocumentationKindInfo(
            kind=spec.kind,
            title=spec.title,
            description=spec.description,
            filename=spec.filename,
            cached=_cache_path(session_dir, spec.kind).is_file(),
        )
        for spec in KINDS.values()
    ]
    return DocumentationStatus(
        session_id=session_id,
        repository=index.repo_name,
        kinds=kinds,
        existing_documents=existing_documents(index),
    )


def generate_documentation(
    session_id: str,
    session_dir: Path,
    index: KnowledgeIndex,
    kind: str,
    refresh: bool = False,
    llm: LLMClient | None = None,
) -> GeneratedDocumentation:
    spec = KINDS.get(kind)
    if spec is None:
        raise UnknownKindError(kind)
    cache = _cache_path(session_dir, spec.kind)
    if not refresh:
        cached = _load_cached(cache)
        if cached is not None:
            return cached

    started = time.monotonic()
    llm = llm or get_llm_client()
    existing = existing_documents(index)
    fact_sheet = build_fact_sheet(index, existing)
    chunks, retrieval_used = gather_evidence(session_dir, index, spec)
    context_text, shown = build_context(chunks)
    raw = llm.generate(_prompt(spec, fact_sheet, context_text), system=SYSTEM_PROMPT)
    validated = validate_answer(raw, shown, index.entities)
    result = GeneratedDocumentation(
        session_id=session_id,
        kind=spec.kind,
        title=spec.title,
        filename=_suggested_filename(spec, existing),
        markdown=validated.answer or raw.strip(),
        existing_documents=existing,
        retrieval_used=retrieval_used,
        sources=validated.sources,
        context=shown,
        references_removed=validated.references_removed,
        model=llm.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=round(time.monotonic() - started, 2),
    )
    _store(cache, result)
    return result


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def existing_documents(index: KnowledgeIndex) -> list[ExistingDocument]:
    """Documentation files already in the repository (never overwritten, spec §28)."""
    found: list[ExistingDocument] = []
    for path, info in sorted(index.files.items()):
        parts = PurePosixPath(path)
        stem = parts.stem.lower()
        suffix = parts.suffix.lower()
        in_doc_dir = any(p.lower() in _DOC_DIRS for p in parts.parts[:-1])
        if stem.startswith("readme"):
            role = "readme"
        elif stem == "contributing":
            role = "contributing"
        elif stem in {"changelog", "changes", "history"}:
            role = "changelog"
        elif in_doc_dir and (suffix in _DOC_EXTENSIONS or suffix == ""):
            role = "docs"
        elif suffix in _DOC_EXTENSIONS and stem in _ROOT_DOC_STEMS and len(parts.parts) == 1:
            role = "other"
        else:
            continue
        found.append(ExistingDocument(path=path, role=role, size_bytes=info.size_bytes))
    return found


def build_fact_sheet(index: KnowledgeIndex, existing: list[ExistingDocument]) -> str:
    """Deterministic facts about the repository, computed from the knowledge base."""
    guide = generate_onboarding(index)
    overview = guide.overview
    lines = [f"Repository: {index.repo_name}"]
    languages = ", ".join(f"{lang} ({n} files)" for lang, n in overview["languages"].items()) or "unknown"
    lines.append(f"Languages: {languages}")
    lines.append(
        f"Size: {overview['source_files']} source files, {overview['classes']} classes, "
        f"{overview['functions']} functions/methods, {overview['test_files']} test files"
    )
    if overview.get("description"):
        lines.append(f"README description: {overview['description']}")
    if overview["entry_points"]:
        lines.append("Entry points: " + ", ".join(overview["entry_points"]))
    dependency_files = [p for p in sorted(index.files) if PurePosixPath(p).name.lower() in _DEPENDENCY_FILES]
    if dependency_files:
        lines.append("Dependency and build files: " + ", ".join(dependency_files))
    if existing:
        lines.append("Existing documentation: " + ", ".join(f"{d.path} ({d.role})" for d in existing))
    else:
        lines.append("Existing documentation: none found")

    packages = guide.architecture.packages
    if packages:
        lines.append("Packages (files / classes / functions):")
        for p in packages[:12]:
            lines.append(f"  - {p['name']}: {p['files']} / {p['classes']} / {p['functions']}")
    hubs = guide.architecture.hubs
    if hubs:
        lines.append("Most imported files: " + ", ".join(f"{h['file']} (imported by {h['imported_by']})" for h in hubs[:6]))
    counts = guide.architecture.relationship_counts
    if counts:
        lines.append("Relationships: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if guide.important_files:
        lines.append("Important files:")
        for f in guide.important_files[:8]:
            symbols = f", symbols: {', '.join(f.symbols[:5])}" if f.symbols else ""
            lines.append(f"  - {f.path}: {'; '.join(f.reasons)}{symbols}")
    routes = _route_entities(index)
    if routes:
        lines.append("Decorated handlers (routes/commands):")
        for e in routes[:20]:
            lines.append(f"  - {e.id} [{', '.join(e.decorators)}]" + (f" {e.signature}" if e.signature else ""))
    tests = sorted(p for p, f in index.files.items() if f.is_test_file)
    if tests:
        lines.append("Test files: " + ", ".join(tests[:12]) + (" ..." if len(tests) > 12 else ""))
    return "\n".join(lines)


def gather_evidence(session_dir: Path, index: KnowledgeIndex, spec: KindSpec) -> tuple[list[RetrievedChunk], bool]:
    """Retrieved chunks for the kind's queries plus deterministic entity excerpts.

    Retrieval is the primary evidence; when the session has no usable vector
    index the document is still drafted from the fact sheet and entity code,
    and the result says so.
    """
    chunks: list[RetrievedChunk] = []
    seen: set[str] = set()
    retrieval_used = True
    for query in spec.queries:
        try:
            retrieved = retrieve(session_dir, query, top_k=_RETRIEVAL_TOP_K)
        except (VectorStoreError, EmbeddingError, OSError):
            retrieval_used = False
            break
        for chunk in retrieved:
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                chunks.append(chunk)
    chunks.sort(key=lambda c: -c.score)

    covered = {(c.file, c.start_line, c.end_line) for c in chunks}
    for number, entity in enumerate(_entity_evidence(index, spec)):
        if len([c for c in chunks if c.chunk_id.startswith("entity-")]) >= _MAX_ENTITY_CHUNKS:
            break
        chunk = entity_chunk(index, entity, f"entity-{number}", _ENTITY_CHUNK_LINES)
        if chunk is None or any(
            f == chunk.file and s <= chunk.end_line and chunk.start_line <= e for f, s, e in covered
        ):
            continue
        chunks.append(chunk)
    return chunks, retrieval_used


def _entity_evidence(index: KnowledgeIndex, spec: KindSpec):
    """Entities whose code is worth showing for this kind, most relevant first."""
    if spec.kind == "api_overview":
        routes = _route_entities(index)
        if routes:
            return routes
    ranked = sorted(
        (e for e in index.entities if e.type in {"class", "function"} and not index.is_test(e.file)),
        key=lambda e: (
            -len(index.incoming["calls"].get(e.id, [])) - len(index.incoming["inherits"].get(e.id, [])),
            not index.is_entry_point(e.file),
            e.id,
        ),
    )
    if spec.kind == "developer_guide":
        entry = [e for e in ranked if index.is_entry_point(e.file)]
        return entry + [e for e in ranked if e not in entry]
    return ranked


def _route_entities(index: KnowledgeIndex):
    result = []
    for entity in index.entities:
        if entity.type not in {"function", "method"} or index.is_test(entity.file):
            continue
        lowered = [d.lower() for d in entity.decorators]
        if any(any(word in d for word in _ROUTE_DECORATOR_WORDS) for d in lowered):
            result.append(entity)
    result.sort(key=lambda e: (e.file, e.start_line))
    return result


# ---------------------------------------------------------------------------
# Prompt, naming, cache
# ---------------------------------------------------------------------------

def _prompt(spec: KindSpec, fact_sheet: str, context_text: str) -> str:
    headings = "\n".join(f"## {h}" for h in spec.headings)
    excerpts = context_text if context_text else "(no code excerpts could be retrieved; rely on the fact sheet and mark gaps as TODO)"
    return (
        f"Document to draft: {spec.title} ({spec.filename}).\n"
        f"{spec.guidance}\n\n"
        f"Use these section headings, in this order:\n{headings}\n\n"
        f"Fact sheet (computed from the repository structure):\n{fact_sheet}\n\n"
        f"Code excerpts:\n\n{excerpts}\n\n"
        f"Write the document."
    )


def _suggested_filename(spec: KindSpec, existing: list[ExistingDocument]) -> str:
    """A name that does not collide with a file already in the repository."""
    taken = {PurePosixPath(d.path).name.lower() for d in existing}
    if spec.filename.lower() not in taken:
        return spec.filename
    stem, suffix = spec.filename.rsplit(".", 1)
    return f"{stem}.codeatlas.{suffix}"


def _cache_path(session_dir: Path, kind: str) -> Path:
    return session_dir / "analysis" / "ai" / f"documentation-{kind}.json"


def _load_cached(path: Path) -> GeneratedDocumentation | None:
    try:
        cached = GeneratedDocumentation.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cached.cached = True
    return cached


def _store(path: Path, result: GeneratedDocumentation) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("Could not cache documentation draft under %s", path.parent)
