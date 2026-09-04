"""Developer onboarding (spec §27, §37): a guided path built from repository evidence.

Every recommendation names real files and symbols from the knowledge base;
stages for which the repository holds no evidence (e.g. no authentication
code) are reported as "not detected" instead of being invented. The whole
thing is deterministic - the optional AI summary lives in the chat pipeline.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from backend.knowledge.store import KnowledgeIndex
from backend.security.models import SecurityReport

_AUTH_WORDS = re.compile(r"auth|login|logout|session|token|jwt|password|credential|permission|oauth|signin|sign_in", re.I)
_DATA_WORDS = re.compile(r"database|db|model|models|schema|migration|repository|repositories|store|storage|orm|sql|query|persist|dao|entity|entities", re.I)
_API_WORDS = re.compile(r"api|route|routes|router|view|views|controller|handler|handlers|endpoint|server|app|main|cli|command", re.I)
_LOGIC_WORDS = re.compile(r"service|services|core|domain|logic|engine|manager|processor|pipeline|worker|task|job|util|utils|helper|helpers", re.I)


class FileRecommendation(BaseModel):
    path: str
    reasons: list[str]
    score: int
    symbols: list[str] = Field(default_factory=list)  # notable classes/functions inside


class KeyConcept(BaseModel):
    name: str
    kind: str  # class | function | package | term
    file: str | None = None
    entity_id: str | None = None
    summary: str | None = None  # first docstring line when available


class ArchitectureSummary(BaseModel):
    packages: list[dict]  # {name, files, classes, functions}
    hubs: list[dict]  # {file, imported_by, imports}
    relationship_counts: dict[str, int]


class ReadingStep(BaseModel):
    order: int
    path: str
    why: str
    symbols: list[str] = Field(default_factory=list)


class OnboardingStage(BaseModel):
    number: str  # "01"
    title: str
    detected: bool
    explanation: str
    files: list[str]
    symbols: list[str]
    questions: list[str]  # good questions for the AI chat


class LearningDay(BaseModel):
    day: int
    theme: str
    files: list[str]
    goal: str


class OnboardingGuide(BaseModel):
    repository: str
    overview: dict  # what the project appears to be, from evidence only
    architecture: ArchitectureSummary
    important_files: list[FileRecommendation]
    reading_order: list[ReadingStep]
    key_concepts: list[KeyConcept]
    stages: list[OnboardingStage]
    learning_path: list[LearningDay]
    note: str = (
        "Generated from the repository's own structure (files, imports, calls, classes, tests, "
        "scanner findings). Stages without evidence are marked as not detected rather than guessed."
    )


def generate_onboarding(index: KnowledgeIndex, security: SecurityReport | None = None) -> OnboardingGuide:
    readme = _readme_path(index)
    important = _important_files(index)
    architecture = _architecture(index)
    concepts = _key_concepts(index)
    stages = _stages(index, readme, important, security)
    reading = _reading_order(index, readme, important)
    return OnboardingGuide(
        repository=index.repo_name,
        overview=_overview(index, readme, security),
        architecture=architecture,
        important_files=important[:12],
        reading_order=reading,
        key_concepts=concepts,
        stages=stages,
        learning_path=_learning_path(stages),
    )


# ---------------------------------------------------------------------------
# Overview / architecture
# ---------------------------------------------------------------------------

def _overview(index: KnowledgeIndex, readme: str | None, security: SecurityReport | None) -> dict:
    languages = Counter(f.language for f in index.files.values() if f.language)
    counts = Counter(e.type for e in index.entities)
    description = None
    if readme is not None:
        description = _readme_description(index, readme)
    return {
        "name": index.repo_name,
        "description": description,
        "description_source": readme if description else None,
        "languages": dict(languages.most_common(6)),
        "source_files": len(index.source_files()),
        "classes": counts.get("class", 0),
        "functions": counts.get("function", 0) + counts.get("method", 0),
        "entry_points": [p for p, f in sorted(index.files.items()) if f.is_entry_point],
        "test_files": sum(1 for f in index.files.values() if f.is_test_file),
        "project_files": [p for p, f in sorted(index.files.items()) if f.is_project_file],
        "security": (
            {"total": security.summary.total, "critical": security.summary.by_severity.get("CRITICAL", 0),
             "high": security.summary.by_severity.get("HIGH", 0), "secrets": security.summary.secrets}
            if security is not None else None
        ),
    }


def _readme_path(index: KnowledgeIndex) -> str | None:
    candidates = [p for p in index.files if PurePosixPath(p).name.lower().startswith("readme")]
    candidates.sort(key=lambda p: (p.count("/"), p))
    return candidates[0] if candidates else None


def _readme_description(index: KnowledgeIndex, readme: str) -> str | None:
    """First paragraph of prose in the README (badges and headings skipped)."""
    entity = index.entity(readme)
    text = None
    if entity is not None and entity.docstring:
        text = entity.docstring
    if text is None:
        return None
    return text.strip()[:400] or None


def _architecture(index: KnowledgeIndex) -> ArchitectureSummary:
    packages: dict[str, Counter] = {}
    for path, entities in index.by_file.items():
        package = index.package_of(path)
        counter = packages.setdefault(package, Counter())
        counter["files"] += 1
        counter["classes"] += sum(1 for e in entities if e.type == "class")
        counter["functions"] += sum(1 for e in entities if e.type in {"function", "method"})
    package_rows = [
        {"name": name, "files": c["files"], "classes": c["classes"], "functions": c["functions"]}
        for name, c in sorted(packages.items(), key=lambda kv: (-kv[1]["files"], kv[0]))
    ]
    imported_by = Counter(e.target for e in index.relationships if e.relation == "imports")
    imports = Counter(e.source for e in index.relationships if e.relation == "imports")
    hubs = [
        {"file": path, "imported_by": imported_by[path], "imports": imports[path]}
        for path, _ in imported_by.most_common(8)
    ]
    return ArchitectureSummary(
        packages=package_rows[:12],
        hubs=hubs,
        relationship_counts=dict(Counter(e.relation for e in index.relationships)),
    )


# ---------------------------------------------------------------------------
# Important files / reading order
# ---------------------------------------------------------------------------

def _important_files(index: KnowledgeIndex) -> list[FileRecommendation]:
    imported_by = Counter(e.target for e in index.relationships if e.relation == "imports")
    called_into: Counter = Counter()
    for edge in index.relationships:
        if edge.relation == "calls":
            source_file, target_file = index.file_of(edge.source), index.file_of(edge.target)
            if source_file != target_file:
                called_into[target_file] += 1
    recommendations = []
    for path in index.source_files():
        if index.is_test(path):
            continue
        entities = index.by_file.get(path, [])
        classes = [e for e in entities if e.type == "class"]
        functions = [e for e in entities if e.type == "function"]
        score = 0
        reasons = []
        if index.is_entry_point(path):
            score += 6
            reasons.append("application entry point")
        if imported_by[path]:
            score += 3 * min(imported_by[path], 5)
            reasons.append(f"imported by {imported_by[path]} file{'s' if imported_by[path] != 1 else ''}")
        if called_into[path]:
            score += min(called_into[path], 6)
            reasons.append(f"called from {called_into[path]} place{'s' if called_into[path] != 1 else ''} in other files")
        if classes:
            score += min(len(classes), 4)
            reasons.append(f"defines {len(classes)} class{'es' if len(classes) != 1 else ''}")
        if functions:
            score += min(len(functions), 4) // 2
        if any(e.parent_classes for e in classes) or any(index.incoming["inherits"].get(c.id) for c in classes):
            score += 2
            reasons.append("part of a class hierarchy")
        if not reasons:
            reasons.append(f"holds {len(entities)} definition{'s' if len(entities) != 1 else ''}")
        symbols = [e.name for e in classes[:3]] + [e.name for e in functions[:3]]
        recommendations.append(FileRecommendation(path=path, reasons=reasons, score=score, symbols=symbols))
    recommendations.sort(key=lambda r: (-r.score, r.path))
    return recommendations


def _reading_order(index: KnowledgeIndex, readme: str | None, important: list[FileRecommendation]) -> list[ReadingStep]:
    steps: list[ReadingStep] = []
    seen: set[str] = set()

    def add(path: str, why: str, symbols: list[str] | None = None) -> None:
        if path in seen:
            return
        seen.add(path)
        steps.append(ReadingStep(order=len(steps) + 1, path=path, why=why, symbols=symbols or []))

    if readme:
        add(readme, "Start with the project's own description of itself.")
    for path, info in sorted(index.files.items()):
        if info.is_project_file and info.name.lower() in {"pyproject.toml", "requirements.txt", "package.json", "setup.py", "dockerfile", "docker-compose.yml"}:
            add(path, "Dependencies and how the project is built or run.")
    for path in [p for p, f in sorted(index.files.items()) if f.is_entry_point]:
        add(path, "Where execution starts; follow the imports from here.", [e.name for e in index.by_file.get(path, [])[:4]])
    # Foundations: files many others import, base classes first.
    for rec in important:
        if any(r.startswith("imported by") for r in rec.reasons) and rec.path not in seen:
            add(rec.path, f"Core module: {', '.join(rec.reasons)}.", rec.symbols)
        if len(steps) >= 8:
            break
    for rec in important:
        if len(steps) >= 10:
            break
        add(rec.path, f"{', '.join(rec.reasons).capitalize()}.", rec.symbols)
    tests = [p for p in sorted(index.files) if index.is_test(p) and index.files[p].language == "python"]
    if tests:
        add(tests[0], "Tests show the intended behaviour and how components are used together.")
    return steps


# ---------------------------------------------------------------------------
# Key concepts / stages / learning path
# ---------------------------------------------------------------------------

def _key_concepts(index: KnowledgeIndex) -> list[KeyConcept]:
    degree: Counter = Counter()
    for edge in index.relationships:
        if edge.relation in {"calls", "inherits"}:
            degree[edge.target] += 1
    concepts: list[KeyConcept] = []
    classes = [e for e in index.entities if e.type == "class" and not index.is_test(e.file)]
    classes.sort(key=lambda e: (-degree[e.id], -len(index.members(e.id)), e.id))
    for entity in classes[:8]:
        concepts.append(KeyConcept(
            name=entity.name, kind="class", file=entity.file, entity_id=entity.id,
            summary=_first_line(entity.docstring),
        ))
    functions = [e for e in index.entities if e.type == "function" and not index.is_test(e.file) and degree[e.id] > 0]
    functions.sort(key=lambda e: (-degree[e.id], e.id))
    for entity in functions[:6]:
        concepts.append(KeyConcept(
            name=entity.name, kind="function", file=entity.file, entity_id=entity.id,
            summary=_first_line(entity.docstring),
        ))
    for package in sorted({index.package_of(p) for p in index.source_files()} - {"(root)"})[:6]:
        concepts.append(KeyConcept(name=package, kind="package", summary=f"Package with {sum(1 for p in index.source_files() if index.package_of(p) == package)} source files"))
    return concepts


def _matching_files(index: KnowledgeIndex, pattern: re.Pattern, include_tests: bool = False) -> list[str]:
    matches = []
    for path in index.source_files():
        if not include_tests and index.is_test(path):
            continue
        entities = index.by_file.get(path, [])
        if pattern.search(path) or any(pattern.search(e.name) for e in entities):
            matches.append(path)
    return matches


def _symbols_for(index: KnowledgeIndex, files: list[str], pattern: re.Pattern | None, limit: int = 8) -> list[str]:
    symbols = []
    for path in files:
        for entity in index.by_file.get(path, []):
            if entity.type in {"class", "function"} and (pattern is None or pattern.search(entity.name) or pattern.search(path)):
                symbols.append(entity.id)
    return symbols[:limit]


def _stages(
    index: KnowledgeIndex, readme: str | None, important: list[FileRecommendation], security: SecurityReport | None
) -> list[OnboardingStage]:
    stages: list[OnboardingStage] = []
    project_files = [p for p, f in sorted(index.files.items()) if f.is_project_file]
    entry = [p for p, f in sorted(index.files.items()) if f.is_entry_point]

    intro_files = ([readme] if readme else []) + project_files[:4] + entry[:2]
    stages.append(OnboardingStage(
        number="01", title="Understand the project", detected=bool(intro_files),
        explanation="What the project is for, how it is built and where it starts.",
        files=intro_files, symbols=[e.name for p in entry[:2] for e in index.by_file.get(p, [])[:3]],
        questions=["What does this project do?", "How do I run this project locally?", "Where does execution start?"],
    ))

    hubs = [r.path for r in important if any(x.startswith("imported by") for x in r.reasons)][:6]
    stages.append(OnboardingStage(
        number="02", title="Learn the architecture", detected=bool(hubs or len(index.source_files()) > 1),
        explanation="The modules everything else depends on, and how packages relate through imports.",
        files=hubs or [r.path for r in important[:5]],
        symbols=[s for r in important[:4] for s in r.symbols][:8],
        questions=["Explain the project architecture.", "Which modules are the core of the codebase?", "How do the packages depend on each other?"],
    ))

    auth_files = _matching_files(index, _AUTH_WORDS)
    stages.append(OnboardingStage(
        number="03", title="Understand authentication", detected=bool(auth_files),
        explanation=(
            "How identity and access are handled: login, sessions, tokens, permissions."
            if auth_files else "No authentication-related files or symbols were detected in this repository."
        ),
        files=auth_files[:6], symbols=_symbols_for(index, auth_files, _AUTH_WORDS),
        questions=["How does authentication work?", "What happens when a user logs in?", "Where are credentials validated?"],
    ))

    logic_files = [p for p in _matching_files(index, _LOGIC_WORDS) if p not in auth_files]
    if not logic_files:
        logic_files = [r.path for r in important if r.path not in auth_files and r.path not in entry][:5]
    stages.append(OnboardingStage(
        number="04", title="Understand business logic", detected=bool(logic_files),
        explanation="Where the domain rules live: services, engines, processors and the classes they build on.",
        files=logic_files[:6], symbols=_symbols_for(index, logic_files, None),
        questions=["What are the main services or components?", "How does the core workflow run end to end?"],
    ))

    data_files = _matching_files(index, _DATA_WORDS)
    stages.append(OnboardingStage(
        number="05", title="Understand persistence", detected=bool(data_files),
        explanation=(
            "How data is stored and retrieved: connections, models, queries and migrations."
            if data_files else "No persistence-related files or symbols were detected in this repository."
        ),
        files=data_files[:6], symbols=_symbols_for(index, data_files, _DATA_WORDS),
        questions=["Where is the database connection initialized?", "How are records read and written?"],
    ))

    test_files = [p for p in sorted(index.files) if index.is_test(p)]
    security_note = ""
    if security is not None and security.summary.total:
        security_note = (
            f" The security scan reported {security.summary.total} finding"
            f"{'s' if security.summary.total != 1 else ''}; review the Security page alongside the tests."
        )
    stages.append(OnboardingStage(
        number="06", title="Understand testing and security", detected=bool(test_files) or security is not None,
        explanation=("How behaviour is verified and where the risky code is." if test_files else "No test files were detected.") + security_note,
        files=test_files[:6] + ([f.file for f in security.findings[:3]] if security else []),
        symbols=[e.id for p in test_files[:3] for e in index.by_file.get(p, []) if e.type == "function"][:6],
        questions=["How are the tests organised?", "Where are the main security risks?", "Which tests cover the authentication flow?"],
    ))
    return stages


def _learning_path(stages: list[OnboardingStage]) -> list[LearningDay]:
    days: list[LearningDay] = []
    for stage in stages:
        if not stage.detected or not stage.files:
            continue
        days.append(LearningDay(
            day=len(days) + 1,
            theme=stage.title,
            files=stage.files[:5],
            goal=stage.explanation.split(".")[0] + ".",
        ))
    return days


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    return text.strip().split("\n")[0][:160]
