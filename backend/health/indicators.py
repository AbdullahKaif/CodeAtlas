"""Codebase Health Indicators (spec §30).

Every indicator is a count or a list computed from the knowledge base, the
scan, the dependency files and the security report. They are deliberately
called *indicators*: none is a standardised score, none is weighted, and the
thresholds behind "very large" are stated in the response so a reader can
judge them. Nothing here calls the LLM.
"""
from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from backend.knowledge.store import KnowledgeIndex
from backend.security.models import SecurityReport

logger = logging.getLogger(__name__)

NOTE = (
    "Codebase Health Indicators are deterministic counts taken from the repository's files, "
    "entities, dependency files and scanner results. They are not standardised industry "
    "scores and carry no weighting; the thresholds used are listed alongside each indicator."
)

VERY_LARGE_FILE_LINES = 500
VERY_LARGE_FUNCTION_LINES = 80
MANY_PARAMETERS = 6
_LIST_CAP = 12
_MAX_DEPENDENCY_FILE_BYTES = 200_000
_DOC_DIRS = {"docs", "doc", "documentation", "wiki"}
_DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
# Scanner languages that are data, markup or build files rather than source code.
_NON_CODE_LANGUAGES = {
    "json", "yaml", "toml", "config", "markdown", "restructuredtext", "text", "xml",
    "html", "css", "docker", "make", "sql", "shell", "powershell",
}

# Dependency name -> (framework label, category). Names are compared lower-case,
# with "-" and "_" treated alike.
FRAMEWORKS: dict[str, tuple[str, str]] = {
    "fastapi": ("FastAPI", "web"), "flask": ("Flask", "web"), "django": ("Django", "web"),
    "starlette": ("Starlette", "web"), "aiohttp": ("aiohttp", "web"), "tornado": ("Tornado", "web"),
    "sanic": ("Sanic", "web"), "bottle": ("Bottle", "web"), "pyramid": ("Pyramid", "web"),
    "uvicorn": ("Uvicorn", "server"), "gunicorn": ("Gunicorn", "server"),
    "sqlalchemy": ("SQLAlchemy", "data"), "alembic": ("Alembic", "data"), "peewee": ("Peewee", "data"),
    "tortoise-orm": ("Tortoise ORM", "data"), "psycopg2": ("psycopg2", "data"), "psycopg": ("psycopg", "data"),
    "pymongo": ("PyMongo", "data"), "redis": ("redis-py", "data"), "asyncpg": ("asyncpg", "data"),
    "pydantic": ("Pydantic", "validation"), "marshmallow": ("marshmallow", "validation"),
    "celery": ("Celery", "tasks"), "rq": ("RQ", "tasks"), "dramatiq": ("Dramatiq", "tasks"),
    "pytest": ("pytest", "testing"), "unittest2": ("unittest", "testing"), "hypothesis": ("Hypothesis", "testing"),
    "click": ("Click", "cli"), "typer": ("Typer", "cli"), "argparse": ("argparse", "cli"),
    "requests": ("Requests", "http-client"), "httpx": ("HTTPX", "http-client"),
    "numpy": ("NumPy", "data-science"), "pandas": ("pandas", "data-science"), "scipy": ("SciPy", "data-science"),
    "scikit-learn": ("scikit-learn", "ml"), "torch": ("PyTorch", "ml"), "tensorflow": ("TensorFlow", "ml"),
    "transformers": ("Transformers", "ml"), "langchain": ("LangChain", "ml"),
    "react": ("React", "web"), "next": ("Next.js", "web"), "vue": ("Vue", "web"), "angular": ("Angular", "web"),
    "@angular/core": ("Angular", "web"), "express": ("Express", "web"), "svelte": ("Svelte", "web"),
    "jest": ("Jest", "testing"), "vitest": ("Vitest", "testing"), "mocha": ("Mocha", "testing"),
}
# Decorator prefixes that imply a framework even when no dependency file names it.
_DECORATOR_HINTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^(app|router|api|blueprint|bp)\.(get|post|put|patch|delete|route|websocket)\b", re.I), "FastAPI or Flask-style routing", "web"),
    (re.compile(r"^(pytest\.|fixture\b)", re.I), "pytest", "testing"),
    (re.compile(r"^(click|typer)\.", re.I), "Click or Typer CLI", "cli"),
    (re.compile(r"^(celery|shared_task|task)\b", re.I), "Celery", "tasks"),
]
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class Framework(BaseModel):
    name: str
    category: str
    evidence: str  # where it was seen: "requirements.txt" or "decorators in app/api.py"


class SecurityIndicators(BaseModel):
    scanned: bool
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    secrets: int = 0
    scanners_ran: list[str] = Field(default_factory=list)
    note: str


class DocumentationIndicators(BaseModel):
    readme_present: bool
    readme_path: str | None = None
    documentation_files: int
    documentation_paths: list[str] = Field(default_factory=list)
    documented_entities: int
    documentable_entities: int
    docstring_coverage: float | None = None  # documented / documentable, None when nothing to measure


class LargeItem(BaseModel):
    id: str
    lines: int


class ManyParameters(BaseModel):
    id: str
    parameters: int


class MaintainabilityIndicators(BaseModel):
    source_files: int
    functions: int  # functions + methods, production code only
    very_large_files: list[LargeItem] = Field(default_factory=list)
    very_large_files_total: int
    very_large_functions: list[LargeItem] = Field(default_factory=list)
    very_large_functions_total: int
    many_parameter_functions: list[ManyParameters] = Field(default_factory=list)
    many_parameter_functions_total: int
    largest_file_lines: int
    longest_function_lines: int
    median_function_lines: int
    thresholds: dict[str, int]


class DependencyIndicators(BaseModel):
    files: list[str] = Field(default_factory=list)
    count: int | None = None  # None when no dependency file could be read
    packages: list[str] = Field(default_factory=list)
    pinned: int | None = None  # exact-version pins among the counted dependencies
    unparsed_files: list[str] = Field(default_factory=list)


class HealthIndicators(BaseModel):
    session_id: str
    repository: str
    security: SecurityIndicators
    documentation: DocumentationIndicators
    maintainability: MaintainabilityIndicators
    dependencies: DependencyIndicators
    frameworks: list[Framework] = Field(default_factory=list)
    note: str = NOTE


def compute_health(
    session_id: str, session_dir: Path, index: KnowledgeIndex, security: SecurityReport | None
) -> HealthIndicators:
    dependencies, frameworks = _dependencies(session_dir, index)
    frameworks = _merge_frameworks(frameworks, _decorator_frameworks(index))
    return HealthIndicators(
        session_id=session_id,
        repository=index.repo_name,
        security=_security(security),
        documentation=_documentation(index),
        maintainability=_maintainability(index),
        dependencies=dependencies,
        frameworks=frameworks,
    )


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def _security(report: SecurityReport | None) -> SecurityIndicators:
    if report is None:
        return SecurityIndicators(scanned=False, note="No scanner ran for this session; nothing is claimed about its security.")
    ran = [s.name for s in report.scanners if s.ran]
    by = report.summary.by_severity
    return SecurityIndicators(
        scanned=bool(ran),
        critical=by.get("CRITICAL", 0),
        high=by.get("HIGH", 0),
        medium=by.get("MEDIUM", 0),
        low=by.get("LOW", 0),
        secrets=report.summary.secrets,
        scanners_ran=ran,
        note=(
            f"Counts from {', '.join(ran)} findings; an absence of findings is not proof of absence of issues."
            if ran else "The scanners were installed but produced no report."
        ),
    )


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

def _documentation(index: KnowledgeIndex) -> DocumentationIndicators:
    readme = None
    doc_paths: list[str] = []
    for path in sorted(index.files):
        parts = PurePosixPath(path)
        if parts.stem.lower().startswith("readme") and (readme is None or path.count("/") < readme.count("/")):
            readme = path
        in_doc_dir = any(p.lower() in _DOC_DIRS for p in parts.parts[:-1])
        if in_doc_dir and parts.suffix.lower() in _DOC_EXTENSIONS:
            doc_paths.append(path)
        elif parts.suffix.lower() in _DOC_EXTENSIONS and len(parts.parts) == 1 and parts.stem.lower() in {
            "contributing", "changelog", "changes", "architecture", "design", "security", "code_of_conduct"
        }:
            doc_paths.append(path)
    documentable = [e for e in index.entities if not index.is_test(e.file)]
    documented = sum(1 for e in documentable if e.docstring and e.docstring.strip())
    return DocumentationIndicators(
        readme_present=readme is not None,
        readme_path=readme,
        documentation_files=len(doc_paths),
        documentation_paths=doc_paths[:_LIST_CAP],
        documented_entities=documented,
        documentable_entities=len(documentable),
        docstring_coverage=round(documented / len(documentable), 3) if documentable else None,
    )


# ---------------------------------------------------------------------------
# Maintainability
# ---------------------------------------------------------------------------

def _maintainability(index: KnowledgeIndex) -> MaintainabilityIndicators:
    source_files = [
        (path, info.line_count or 0)
        for path, info in index.files.items()
        if info.language and info.language not in _NON_CODE_LANGUAGES and not index.is_test(path)
    ]
    if not source_files:  # no scan metadata: fall back to files that hold entities
        source_files = [(path, 0) for path in index.by_file if not index.is_test(path)]
    large_files = sorted(
        (LargeItem(id=p, lines=n) for p, n in source_files if n > VERY_LARGE_FILE_LINES),
        key=lambda x: -x.lines,
    )
    callables = [
        e for e in index.entities
        if e.type in {"function", "method"} and not index.is_test(e.file)
    ]
    lengths = sorted(e.end_line - e.start_line + 1 for e in callables)
    large_functions = sorted(
        (LargeItem(id=e.id, lines=e.end_line - e.start_line + 1) for e in callables
         if e.end_line - e.start_line + 1 > VERY_LARGE_FUNCTION_LINES),
        key=lambda x: -x.lines,
    )
    many_params = sorted(
        (ManyParameters(id=e.id, parameters=len(_real_parameters(e.parameters)))
         for e in callables if len(_real_parameters(e.parameters)) > MANY_PARAMETERS),
        key=lambda x: -x.parameters,
    )
    return MaintainabilityIndicators(
        source_files=len(source_files),
        functions=len(callables),
        very_large_files=large_files[:_LIST_CAP],
        very_large_files_total=len(large_files),
        very_large_functions=large_functions[:_LIST_CAP],
        very_large_functions_total=len(large_functions),
        many_parameter_functions=many_params[:_LIST_CAP],
        many_parameter_functions_total=len(many_params),
        largest_file_lines=max((n for _, n in source_files), default=0),
        longest_function_lines=lengths[-1] if lengths else 0,
        median_function_lines=lengths[len(lengths) // 2] if lengths else 0,
        thresholds={
            "very_large_file_lines": VERY_LARGE_FILE_LINES,
            "very_large_function_lines": VERY_LARGE_FUNCTION_LINES,
            "many_parameters": MANY_PARAMETERS,
        },
    )


def _real_parameters(parameters: list[str]) -> list[str]:
    return [p for p in parameters if p.split(":", 1)[0].split("=", 1)[0].strip() not in {"self", "cls"}]


# ---------------------------------------------------------------------------
# Dependencies and frameworks
# ---------------------------------------------------------------------------

def _dependencies(session_dir: Path, index: KnowledgeIndex) -> tuple[DependencyIndicators, list[Framework]]:
    repo = session_dir / "repository"
    files: list[str] = []
    unparsed: list[str] = []
    packages: dict[str, bool] = {}  # name -> pinned
    frameworks: list[Framework] = []
    for path in sorted(index.files):
        name = PurePosixPath(path).name.lower()
        parser = _PARSERS.get(name)
        if parser is None:
            continue
        files.append(path)
        text = _read(repo / path)
        if text is None:
            unparsed.append(path)
            continue
        try:
            found = parser(text)
        except Exception:  # noqa: BLE001 - a malformed dependency file is reported, never fatal
            logger.info("Could not parse dependency file %s", path)
            unparsed.append(path)
            continue
        for package, pinned in found:
            key = package.lower().replace("_", "-")
            packages[key] = packages.get(key, False) or pinned
            hit = FRAMEWORKS.get(key)
            if hit and all(f.name != hit[0] for f in frameworks):
                frameworks.append(Framework(name=hit[0], category=hit[1], evidence=path))
    counted = len(packages) if files and len(unparsed) < len(files) else None
    return (
        DependencyIndicators(
            files=files,
            count=counted,
            packages=sorted(packages)[:30],
            pinned=sum(1 for p in packages.values() if p) if counted is not None else None,
            unparsed_files=unparsed,
        ),
        frameworks,
    )


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_DEPENDENCY_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _parse_requirements(text: str) -> list[tuple[str, bool]]:
    found = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http://", "https://", "file:")):
            continue
        match = _REQUIREMENT_NAME.match(line)
        if match:
            found.append((match.group(1), "==" in line))
    return found


def _parse_pyproject(text: str) -> list[tuple[str, bool]]:
    data = tomllib.loads(text)
    found: list[tuple[str, bool]] = []
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    specs = list(project.get("dependencies", []) or [])
    for group in (project.get("optional-dependencies") or {}).values():
        specs.extend(group or [])
    for spec in specs:
        if isinstance(spec, str):
            match = _REQUIREMENT_NAME.match(spec)
            if match:
                found.append((match.group(1), "==" in spec))
    poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool"), dict) else {}
    for section in ("dependencies", "dev-dependencies"):
        for name, spec in (poetry.get(section) or {}).items():
            if name.lower() == "python":
                continue
            version = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
            found.append((name, bool(version) and version[0].isdigit()))
    return found


def _parse_package_json(text: str) -> list[tuple[str, bool]]:
    import json

    data = json.loads(text)
    found = []
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            found.append((name, isinstance(version, str) and bool(version) and version[0].isdigit()))
    return found


def _parse_go_mod(text: str) -> list[tuple[str, bool]]:
    found = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):].strip()
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            found.append((parts[0], True))
    return found


_PARSERS = {
    "requirements.txt": _parse_requirements,
    "requirements-dev.txt": _parse_requirements,
    "constraints.txt": _parse_requirements,
    "pyproject.toml": _parse_pyproject,
    "package.json": _parse_package_json,
    "go.mod": _parse_go_mod,
}


def _decorator_frameworks(index: KnowledgeIndex) -> list[Framework]:
    found: dict[str, Framework] = {}
    for entity in index.entities:
        if index.is_test(entity.file):
            continue
        for decorator in entity.decorators:
            text = decorator.lstrip("@")
            for pattern, name, category in _DECORATOR_HINTS:
                if pattern.search(text) and name not in found:
                    found[name] = Framework(name=name, category=category, evidence=f"decorators in {entity.file}")
    return list(found.values())


def _merge_frameworks(primary: list[Framework], hints: list[Framework]) -> list[Framework]:
    merged = list(primary)
    categories = {f.category for f in primary}
    for hint in hints:
        # A dependency-file hit already explains the category; the decorator hint adds nothing.
        if hint.category in categories and hint.category in {"web", "testing", "cli", "tasks"}:
            continue
        merged.append(hint)
    return merged
