"""Repository scanner: discovers relevant source files without executing anything.

Repository contents are treated as untrusted input - the scanner only stats files
and reads bytes for line counting / binary sniffing, never executes or imports them.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from backend.config import settings

# Directories that are never worth scanning (VCS internals, dependencies, build output).
IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    "venv", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".eggs", "site-packages",
    "dist", "build", "out", "target", "coverage", "htmlcov",
    ".idea", ".vscode", ".next", ".nuxt", ".terraform", ".gradle", ".cache",
    "vendor",
}

LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".go": "go", ".rb": "ruby", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".kt": "kotlin", ".swift": "swift", ".scala": "scala",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell",
    ".sql": "sql", ".html": "html", ".css": "css", ".scss": "css",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "config", ".cfg": "config",
    ".md": "markdown", ".rst": "restructuredtext", ".txt": "text", ".xml": "xml",
}

# Extensionless / specially named files mapped to a language.
SPECIAL_FILENAME_LANGUAGE = {
    "dockerfile": "docker",
    "makefile": "make",
}

# Known-binary extensions are skipped without opening the file.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svgz", ".webp", ".tiff",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac", ".ogg", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".lib", ".obj",
    ".pyc", ".pyd", ".pyo", ".class", ".wasm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3", ".pkl", ".pickle", ".npy", ".npz", ".parquet",
    ".iso", ".img", ".dmg",
}

# Junk / editor / temporary files skipped by name suffix.
TEMP_FILE_SUFFIXES = (".log", ".tmp", ".bak", ".swp", ".swo", "~", ".ds_store")

# Well-known project configuration files (matched case-insensitively by name).
PROJECT_FILES = {
    "requirements.txt", "requirements-dev.txt", "constraints.txt",
    "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "pipfile.lock", "poetry.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "tsconfig.json",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "makefile", "go.mod", "go.sum", "cargo.toml", "gemfile", "pom.xml", "build.gradle",
    "readme", "readme.md", "readme.rst", "readme.txt",
    "license", "license.md", "license.txt",
    ".gitignore", ".env.example", "manage.py",
}

# Likely application entry points (matched case-insensitively by name).
ENTRY_POINT_NAMES = {
    "main.py", "app.py", "application.py", "manage.py", "wsgi.py", "asgi.py",
    "server.py", "run.py", "cli.py", "__main__.py",
    "index.js", "index.ts", "server.js", "app.js", "main.go", "main.rs",
}

class FileInfo(BaseModel):
    """Metadata for one scanned file. path is POSIX-style, relative to the repo root."""

    path: str
    name: str
    extension: str
    language: str | None = None
    size_bytes: int
    line_count: int | None = None  # only computed for recognized files within the size limit
    is_entry_point: bool = False
    is_project_file: bool = False
    is_test_file: bool = False


class ScanSummary(BaseModel):
    """Scan counters.

    Invariant: total_files_seen == files_included + files_skipped_binary + files_skipped_other.
    files_skipped_large counts files that ARE included but whose content was not read.
    Symlinks are never counted anywhere.
    """

    total_files_seen: int
    files_included: int
    files_skipped_binary: int
    files_skipped_large: int
    files_skipped_other: int  # temp/junk suffixes, unreadable files
    dirs_skipped: int
    truncated: bool = False  # True when the walk stopped before covering every file


class RepositoryScan(BaseModel):
    root: str
    files: list[FileInfo]
    languages: dict[str, int]
    entry_points: list[str]
    project_files: list[str]
    total_size_bytes: int
    summary: ScanSummary


def scan_repository(repo_path: Path | str) -> RepositoryScan:
    """Walk the repository and collect metadata for every relevant file."""
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Repository path does not exist: {repo_path}")

    # Stop walking entirely once far more files were seen than can be included,
    # so a million-file repo cannot make us stat/sniff every single one.
    max_seen = settings.max_files * 3

    files: list[FileInfo] = []
    languages: Counter[str] = Counter()
    total_size = 0
    seen = skipped_binary = skipped_large = skipped_other = skipped_dirs = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        kept_dirs = []
        for d in dirnames:
            if d.lower() in IGNORED_DIRS or _is_virtualenv(Path(dirpath) / d):
                skipped_dirs += 1
            else:
                kept_dirs.append(d)
        dirnames[:] = sorted(kept_dirs)

        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            if full.is_symlink():
                continue
            # Checked before processing, so a scan that exactly fills the cap
            # is not flagged as truncated.
            if len(files) >= settings.max_files or seen >= max_seen:
                truncated = True
                break
            seen += 1
            try:
                size = full.stat().st_size
            except OSError:
                skipped_other += 1
                continue

            lower = filename.lower()
            ext = full.suffix.lower()
            if ext in BINARY_EXTENSIONS:
                skipped_binary += 1
                continue
            if lower.endswith(TEMP_FILE_SUFFIXES):
                skipped_other += 1
                continue
            # Cheap 8 KB sniff applies to every file regardless of size, so large
            # binaries with unknown extensions cannot slip into the inventory.
            if _looks_binary(full):
                skipped_binary += 1
                continue

            language = LANGUAGE_BY_EXTENSION.get(ext) or SPECIAL_FILENAME_LANGUAGE.get(lower)
            is_project = lower in PROJECT_FILES
            rel = full.relative_to(root).as_posix()

            line_count = None
            if size > settings.max_file_size_bytes:
                skipped_large += 1
            elif language is not None:
                line_count, is_binary = _count_lines(full)
                if is_binary:
                    skipped_binary += 1
                    continue

            files.append(
                FileInfo(
                    path=rel,
                    name=filename,
                    extension=ext,
                    language=language,
                    size_bytes=size,
                    line_count=line_count,
                    is_entry_point=lower in ENTRY_POINT_NAMES,
                    is_project_file=is_project,
                    is_test_file=_is_test_file(rel, lower),
                )
            )
            total_size += size
            if language is not None:
                languages[language] += 1
        if truncated:
            break

    files.sort(key=lambda f: f.path)
    return RepositoryScan(
        root=root.name,
        files=files,
        languages=dict(languages.most_common()),
        entry_points=[f.path for f in files if f.is_entry_point],
        project_files=[f.path for f in files if f.is_project_file],
        total_size_bytes=total_size,
        summary=ScanSummary(
            total_files_seen=seen,
            files_included=len(files),
            files_skipped_binary=skipped_binary,
            files_skipped_large=skipped_large,
            files_skipped_other=skipped_other,
            dirs_skipped=skipped_dirs,
            truncated=truncated,
        ),
    )


def _is_virtualenv(path: Path) -> bool:
    """Detect Python virtual environments regardless of directory name."""
    try:
        return (path / "pyvenv.cfg").is_file()
    except OSError:
        return False


def _is_test_file(rel_path: str, lower_name: str) -> bool:
    if lower_name.startswith("test_"):
        return True
    if lower_name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts")):
        return True
    parents = PurePosixPath(rel_path).parts[:-1]
    return any(part.lower() in {"test", "tests", "__tests__"} for part in parents)


_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _count_lines(path: Path) -> tuple[int | None, bool]:
    """Count lines in chunks. Returns (line_count, is_binary).

    UTF-16 files legitimately contain null bytes, so they are decoded instead of
    being misclassified as binary.
    """
    lines = 0
    last_chunk = b""
    try:
        with open(path, "rb") as fh:
            head = fh.read(2)
            if head in _UTF16_BOMS:
                text = (head + fh.read()).decode("utf-16", errors="replace")
                if not text:
                    return 0, False
                return text.count("\n") + (0 if text.endswith("\n") else 1), False
            chunk = head + fh.read(65534)
            while chunk:
                if b"\x00" in chunk:
                    return None, True
                lines += chunk.count(b"\n")
                last_chunk = chunk
                chunk = fh.read(65536)
    except OSError:
        return None, False
    if last_chunk and not last_chunk.endswith(b"\n"):
        lines += 1
    return lines, False


def _looks_binary(path: Path) -> bool:
    """Cheap binary sniff: null byte in the first 8 KB (UTF-16 BOMs excepted)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return True
    if head.startswith(_UTF16_BOMS):
        return False
    return b"\x00" in head
