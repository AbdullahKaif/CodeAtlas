"""Suggested tests for one function or method (spec §29).

The target's code, the code it calls, the code that calls it, any existing
tests that exercise it and the project's testing conventions are gathered
from the knowledge base and shown to the local model as numbered excerpts.
The model returns one test module covering normal cases, edge cases, invalid
inputs and error conditions. The result is a suggestion: nothing is written
to the cloned repository, and every citation is validated.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from backend.impact.explain import entity_chunk
from backend.knowledge.store import KnowledgeIndex
from backend.llm.ollama_client import LLMClient, get_llm_client
from backend.parser.models import Entity
from backend.rag.embeddings import EmbeddingError
from backend.rag.models import RetrievedChunk
from backend.rag.prompts import build_context
from backend.rag.retriever import retrieve
from backend.rag.sources import SourceReference, validate_answer
from backend.rag.vector_store import VectorStoreError

logger = logging.getLogger(__name__)

DISCLAIMER = "AI-generated test suggestions - nothing has been written to the repository. Review and adapt before adding."

_TARGET_MAX_LINES = 150
_CLASS_MAX_LINES = 60
_RELATED_MAX_LINES = 40
_MAX_CALLEES = 4
_MAX_CALLERS = 3
_MAX_EXISTING_TESTS = 4
_RETRIEVED_TESTS = 3
_FENCE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)
_ASSUMPTIONS = re.compile(r"^\s*\**\s*assumptions?\s*\**\s*:[ 	]*\**[ 	]*", re.IGNORECASE | re.MULTILINE)
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.MULTILINE)
_UNITTEST_IMPORT = re.compile(r"^\s*(?:import\s+unittest|from\s+unittest\b)", re.MULTILINE)
_PYTEST_USE = re.compile(r"\bpytest\b|^\s*def\s+test_", re.MULTILINE)

CategoryName = Literal["normal", "edge", "invalid_input", "error_conditions"]
CATEGORIES: list[tuple[CategoryName, str, tuple[str, ...]]] = [
    ("normal", "Normal cases", ("normal",)),
    ("edge", "Edge cases", ("edge",)),
    ("invalid_input", "Invalid inputs", ("invalid",)),
    ("error_conditions", "Error conditions", ("error", "exception", "raises", "fail")),
]

SYSTEM_PROMPT = """You are CodeAtlas's test suggestion assistant. Propose tests for ONE function or method of ONE repository, using ONLY the code excerpts provided.

Rules:
1. Test the target's behaviour as the excerpts show it. Never assume parameters, return values, exceptions or collaborators the excerpts do not show; when a behaviour is unknown, write the test against what is visible and note the gap under "Assumptions:".
2. Follow the project's testing conventions given in the prompt (framework, file naming, fixtures). When the project has no visible conventions, use pytest with plain functions.
3. Cover four categories, in this order, each introduced by a comment line: `# Normal cases`, `# Edge cases`, `# Invalid inputs`, `# Error conditions`. Every test function name starts with `test_` and says what it checks. If a category does not apply, keep the comment and add one line explaining why.
4. Import the target from its real module path (derived from its file path). Do not test private helpers or unrelated code.
5. Output exactly:
   - one short paragraph describing what the tests cover;
   - ONE fenced ```python code block containing the complete test module;
   - a line "Assumptions:" followed by anything the excerpts did not settle (or "none");
   - a "Sources:" section listing the excerpts you relied on, one per line, exactly as:
     - <file path>: lines <start>-<end>
"""


class TestingConventions(BaseModel):
    framework: Literal["pytest", "unittest", "unknown"]
    test_files: int
    test_directories: list[str] = Field(default_factory=list)
    naming_pattern: str | None = None  # e.g. "test_*.py"
    fixtures_file: str | None = None  # a conftest.py, when present
    example_test_file: str | None = None
    evidence: list[str] = Field(default_factory=list)  # why the framework was chosen


class TestCategory(BaseModel):
    name: CategoryName
    label: str
    tests: list[str] = Field(default_factory=list)  # test function names found under the category
    covered: bool = False


class TestTarget(BaseModel):
    id: str
    type: str
    name: str
    file: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    parent: str | None = None


class TestSuggestion(BaseModel):
    session_id: str
    target: TestTarget
    conventions: TestingConventions
    suggested_file: str  # where the tests would live; never written by CodeAtlas
    suggested_file_exists: bool
    explanation: str
    code: str  # the suggested test module; empty when the model produced no code block
    categories: list[TestCategory] = Field(default_factory=list)
    assumptions: str = ""
    existing_tests: list[str] = Field(default_factory=list)  # test entities that already exercise the target
    sources: list[SourceReference] = Field(default_factory=list)
    context: list[RetrievedChunk] = Field(default_factory=list)
    references_removed: int = 0
    model: str
    cached: bool = False
    generated_at: str
    duration_seconds: float
    disclaimer: str = DISCLAIMER


class UnknownTargetError(Exception):
    pass


class UnsupportedTargetError(Exception):
    """The target exists but is not a function or method."""


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def detect_conventions(index: KnowledgeIndex) -> TestingConventions:
    """The project's testing setup, from file names and imports only."""
    test_files = sorted(p for p, f in index.files.items() if f.is_test_file)
    if not test_files:
        test_files = sorted(p for p in index.by_file if index.is_test(p))
    directories = sorted({str(PurePosixPath(p).parent) for p in test_files})
    conftests = sorted(p for p in index.files if PurePosixPath(p).name == "conftest.py")
    names = [PurePosixPath(p).name for p in test_files]
    naming = None
    if names and all(n.startswith("test_") for n in names):
        naming = "test_*.py"
    elif names and all(n.endswith("_test.py") for n in names):
        naming = "*_test.py"

    evidence: list[str] = []
    framework: Literal["pytest", "unittest", "unknown"] = "unknown"
    unittest_hits = 0
    pytest_hits = 0
    for path in test_files:
        source = _test_file_text(index, path)
        if _UNITTEST_IMPORT.search(source):
            unittest_hits += 1
        if _PYTEST_USE.search(source):
            pytest_hits += 1
    if conftests:
        framework = "pytest"
        evidence.append(f"{conftests[0]} present")
    if any(PurePosixPath(p).name.lower() in {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"} for p in index.files):
        evidence.append("pytest configuration file candidates present")
    if pytest_hits and not unittest_hits:
        framework = "pytest"
        evidence.append(f"{pytest_hits} test file(s) use pytest-style test functions")
    elif unittest_hits and not pytest_hits:
        framework = "unittest"
        evidence.append(f"{unittest_hits} test file(s) import unittest")
    elif unittest_hits and pytest_hits:
        framework = "pytest" if pytest_hits >= unittest_hits else "unittest"
        evidence.append(f"mixed: {pytest_hits} pytest-style, {unittest_hits} unittest-based test file(s)")
    if framework == "unknown" and test_files:
        evidence.append("test files found but no framework markers recognised")
    if not test_files:
        evidence.append("no test files found")

    return TestingConventions(
        framework=framework,
        test_files=len(test_files),
        test_directories=directories,
        naming_pattern=naming,
        fixtures_file=conftests[0] if conftests else None,
        example_test_file=test_files[0] if test_files else None,
        evidence=evidence,
    )


def generate_tests(
    session_id: str,
    session_dir: Path,
    index: KnowledgeIndex,
    target_id: str,
    refresh: bool = False,
    llm: LLMClient | None = None,
) -> TestSuggestion:
    target = index.entity(target_id)
    if target is None:
        raise UnknownTargetError(target_id)
    if target.type not in {"function", "method"}:
        raise UnsupportedTargetError(target.type)

    cache = _cache_path(session_dir, target.id)
    if not refresh:
        cached = _load_cached(cache)
        if cached is not None:
            return cached

    started = time.monotonic()
    llm = llm or get_llm_client()
    conventions = detect_conventions(index)
    existing = _existing_tests(index, target)
    chunks = _evidence(session_dir, index, target, existing)
    context_text, shown = build_context(chunks)
    suggested_file = _suggested_file(index, target, conventions)
    raw = llm.generate(
        _prompt(target, conventions, existing, suggested_file, context_text), system=SYSTEM_PROMPT
    )
    validated = validate_answer(raw, shown, index.entities)
    explanation, code, assumptions = _parse(validated.answer or raw)
    result = TestSuggestion(
        session_id=session_id,
        target=_target_summary(target),
        conventions=conventions,
        suggested_file=suggested_file,
        suggested_file_exists=suggested_file in index.files or suggested_file in index.by_file,
        explanation=explanation,
        code=code,
        categories=_categories(code),
        assumptions=assumptions,
        existing_tests=[e.id for e in existing],
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

def _existing_tests(index: KnowledgeIndex, target: Entity) -> list[Entity]:
    """Test functions that call the target, or import its file, statically."""
    found: dict[str, Entity] = {}
    for edge in index.incoming["calls"].get(target.id, []):
        caller = index.entity(edge.source)
        if caller is not None and caller.type != "file" and index.is_test(caller.file):
            found[caller.id] = caller
    if target.parent:
        for edge in index.incoming["calls"].get(target.parent, []):
            caller = index.entity(edge.source)
            if caller is not None and caller.type != "file" and index.is_test(caller.file):
                found.setdefault(caller.id, caller)
    if not found:
        for edge in index.incoming["imports"].get(target.file, []):
            if index.is_test(index.file_of(edge.source)):
                for entity in index.by_file.get(index.file_of(edge.source), []):
                    if entity.type == "function" and entity.name.startswith("test"):
                        found.setdefault(entity.id, entity)
    return sorted(found.values(), key=lambda e: (e.file, e.start_line))[:_MAX_EXISTING_TESTS]


def _evidence(session_dir: Path, index: KnowledgeIndex, target: Entity, existing: list[Entity]) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    target_chunk = entity_chunk(index, target, "target", _TARGET_MAX_LINES)
    if target_chunk is not None:
        chunks.append(target_chunk)

    if target.parent:
        parent = index.entity(target.parent)
        if parent is not None and parent.type == "class":
            chunk = entity_chunk(index, parent, "enclosing-class", _CLASS_MAX_LINES)
            if chunk is not None:
                chunks.append(chunk)

    seen = {target.id, target.parent}
    callees = [index.entity(e.target) for e in index.outgoing["calls"].get(target.id, [])]
    for number, entity in enumerate(e for e in callees if e is not None and e.id not in seen):
        if number >= _MAX_CALLEES:
            break
        seen.add(entity.id)
        chunk = entity_chunk(index, entity, f"callee-{number}", _RELATED_MAX_LINES)
        if chunk is not None:
            chunks.append(chunk)
    callers = [index.entity(e.source) for e in index.incoming["calls"].get(target.id, [])]
    production_callers = [c for c in callers if c is not None and c.type != "file" and not index.is_test(c.file)]
    for number, entity in enumerate(c for c in production_callers if c.id not in seen):
        if number >= _MAX_CALLERS:
            break
        seen.add(entity.id)
        chunk = entity_chunk(index, entity, f"caller-{number}", _RELATED_MAX_LINES)
        if chunk is not None:
            chunks.append(chunk)

    for number, entity in enumerate(existing):
        chunk = entity_chunk(index, entity, f"existing-test-{number}", _RELATED_MAX_LINES)
        if chunk is not None:
            chunks.append(chunk)
    chunks.extend(_retrieved_tests(session_dir, index, target, chunks))
    return chunks


def _retrieved_tests(session_dir: Path, index: KnowledgeIndex, target: Entity, have: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """A few test-file chunks that read like tests for the target, when an index exists."""
    query = f"tests for {target.name} in {target.file}"
    if target.signature:
        query += f" {target.signature}"
    try:
        retrieved = retrieve(session_dir, query, top_k=_RETRIEVED_TESTS * 3)
    except (VectorStoreError, EmbeddingError, OSError):
        return []
    result = []
    for chunk in retrieved:
        if not index.is_test(chunk.file):
            continue
        if any(c.file == chunk.file and c.start_line <= chunk.end_line and chunk.start_line <= c.end_line for c in have):
            continue
        result.append(chunk)
        if len(result) >= _RETRIEVED_TESTS:
            break
    return result


def _test_file_text(index: KnowledgeIndex, path: str) -> str:
    """Enough of a test file to recognise its framework: entity sources and decorators."""
    parts = []
    for entity in index.by_file.get(path, []):
        if entity.source_code:
            parts.append(entity.source_code)
        parts.extend(entity.decorators)
        if entity.parent_classes:
            parts.append(" ".join(entity.parent_classes))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Prompt, parsing, naming
# ---------------------------------------------------------------------------

def _prompt(target: Entity, conventions: TestingConventions, existing: list[Entity], suggested_file: str, context_text: str) -> str:
    module = _module_path(target.file)
    top_level = target.id.split("::", 1)[1].split(".")[0] if "::" in target.id else target.name
    lines = [
        f"Target: {target.id} ({target.type} in {target.file}, lines {target.start_line}-{target.end_line})",
        f"Signature: {target.signature or target.name}",
        f"Import path: from {module} import {top_level}",
    ]
    if target.docstring:
        lines.append(f"Docstring: {target.docstring.strip().splitlines()[0]}")
    lines.append(
        f"Testing conventions: framework {conventions.framework}; {conventions.test_files} test file(s)"
        + (f" under {', '.join(conventions.test_directories)}" if conventions.test_directories else "")
        + (f"; naming {conventions.naming_pattern}" if conventions.naming_pattern else "")
        + (f"; shared fixtures in {conventions.fixtures_file}" if conventions.fixtures_file else "")
        + "."
    )
    lines.append(f"Suggested test file: {suggested_file}")
    if existing:
        lines.append("Existing tests that already exercise the target: " + ", ".join(e.id for e in existing) + ". Do not duplicate them; add what they miss.")
    else:
        lines.append("No existing tests exercise the target.")
    lines.append(f"\nCode excerpts (block [1] is the target):\n\n{context_text}\n\nWrite the suggested tests.")
    return "\n".join(lines)


def _parse(raw: str) -> tuple[str, str, str]:
    match = _FENCE.search(raw)
    if match is None:
        explanation, assumptions = _split_assumptions(raw)
        code = ""
    else:
        code = match.group(1).rstrip("\n")
        before = raw[: match.start()].strip()
        after = raw[match.end():].strip()
        explanation, pre = _split_assumptions(before)
        _, post = _split_assumptions(after) if _ASSUMPTIONS.search(after) else ("", "")
        assumptions = (pre + "\n" + post).strip()
    if assumptions.strip().rstrip(".").lower() == "none":
        assumptions = ""
    return explanation.strip(), code, assumptions


def _split_assumptions(text: str) -> tuple[str, str]:
    match = _ASSUMPTIONS.search(text)
    if match is None:
        return text, ""
    return text[: match.start()].strip(), text[match.end():].strip()


def _categories(code: str) -> list[TestCategory]:
    """Which of the four categories the code covers, from its section comments and test names."""
    result: list[TestCategory] = []
    if not code:
        return [TestCategory(name=name, label=label) for name, label, _ in CATEGORIES]
    sections: list[tuple[str, str]] = []  # (heading comment, body)
    current_heading = ""
    current: list[str] = []
    for line in code.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#") and any(word in stripped.lower() for _, _, words in CATEGORIES for word in words):
            sections.append((current_heading, "\n".join(current)))
            current_heading, current = stripped.lower(), []
        else:
            current.append(line)
    sections.append((current_heading, "\n".join(current)))
    for name, label, words in CATEGORIES:
        tests: list[str] = []
        for heading, body in sections:
            if any(word in heading for word in words):
                tests.extend(_TEST_DEF.findall(body))
        if not tests:
            tests = [t for t in _TEST_DEF.findall(code) if any(word in t.lower() for word in words)]
        result.append(TestCategory(name=name, label=label, tests=tests, covered=bool(tests)))
    return result


def _suggested_file(index: KnowledgeIndex, target: Entity, conventions: TestingConventions) -> str:
    """The existing test file for the target's module when there is one, else a conventional new path."""
    stem = PurePosixPath(target.file).stem
    candidates = [f"test_{stem}.py", f"{stem}_test.py"]
    for path in sorted(set(index.files) | set(index.by_file)):
        if PurePosixPath(path).name in candidates and index.is_test(path):
            return path
    directory = conventions.test_directories[0] if conventions.test_directories else "tests"
    name = f"{stem}_test.py" if conventions.naming_pattern == "*_test.py" else f"test_{stem}.py"
    return f"{directory}/{name}" if directory not in {"", "."} else name


def _module_path(file: str) -> str:
    path = PurePosixPath(file)
    parts = list(path.parts[:-1]) + [path.stem]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or path.stem


def _target_summary(entity: Entity) -> TestTarget:
    return TestTarget(
        id=entity.id,
        type=entity.type,
        name=entity.name,
        file=entity.file,
        start_line=entity.start_line,
        end_line=entity.end_line,
        signature=entity.signature,
        docstring=entity.docstring,
        parent=entity.parent,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(session_dir: Path, target_id: str) -> Path:
    key = hashlib.sha1(target_id.encode("utf-8")).hexdigest()[:16]
    return session_dir / "analysis" / "ai" / f"tests-{key}.json"


def _load_cached(path: Path) -> TestSuggestion | None:
    try:
        cached = TestSuggestion.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cached.cached = True
    return cached


def _store(path: Path, result: TestSuggestion) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("Could not cache test suggestion under %s", path.parent)
