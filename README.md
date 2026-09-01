# CodeAtlas

**Understand. Secure. Onboard.**

CodeAtlas is a local-first, AI-powered code intelligence platform. Paste a GitHub
repository URL and CodeAtlas clones it locally, parses it, indexes it, scans it for
security issues, and lets you explore it through an AI chat, architecture graph,
impact analysis and a guided onboarding path - without your source code ever
leaving your machine.

## Problem

Joining an unfamiliar codebase is slow and risky: you don't know how it is
structured, where the important code lives, what is vulnerable, or what breaks
when you change something. CodeAtlas answers those questions.

## How it works

```
GitHub URL -> Clone (GitPython) -> Scan -> Parse (Tree-sitter)
    -> Knowledge base -> Chunk -> Embed (BGE) -> FAISS
    -> Retrieve -> Qwen3-Coder (Ollama, local) -> AI insights
Security: Semgrep + Gitleaks (local scans) -> normalized findings -> AI explanations
```

Everything runs locally. Repository content is never sent to an external API.

## Status

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Project setup, GitHub cloning, repository scanner | Done |
| 2 | Tree-sitter parsing, entities, relationships | Planned |
| 3 | Chunking, embeddings, FAISS | Planned |
| 4 | Ollama + Qwen3-Coder RAG chat | Planned |
| 5 | Semgrep + Gitleaks security engine | Planned |
| 6 | Architecture graph, impact analysis, onboarding | Planned |
| 7 | Fix suggestions, documentation, test generation | Planned |
| 8 | Frontend polish, privacy, demo | Planned |

## Tech stack

- **Backend:** Python 3.11+, FastAPI, GitPython, Tree-sitter, Sentence Transformers
  (BAAI/bge-small-en-v1.5), FAISS (CPU), Ollama + Qwen3-Coder, Semgrep, Gitleaks
- **Frontend:** Next.js, React, TypeScript, Tailwind CSS, React Flow

## Backend setup (current)

Requires Python 3.11+ and `git` on your PATH.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements-dev.txt
```

## Run the API

```bash
uvicorn backend.main:app --reload
```

Then open http://127.0.0.1:8000/docs for the interactive API docs.

### Current endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/analyze` | Clone a GitHub repo into an isolated session and scan it |
| GET | `/api/repository/{session_id}/overview` | Read back the scan result |
| DELETE | `/api/session/{session_id}` | Delete all session data (privacy) |
| GET | `/api/health` | Health check |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d "{\"repo_url\": \"https://github.com/pallets/click\"}"
```

## Tests

```bash
pytest
```

The test-suite uses a local fixture repository and monkeypatched clones - it does
not require network access.

## Privacy model

- All processing happens locally.
- Each analysis lives in an isolated `session_<id>/` directory under the system
  temp folder (`%TEMP%\codeatlas\` on Windows) - deliberately **outside** the
  project directory so cloud-sync clients (OneDrive/Dropbox) never upload cloned
  code. Override the location with `CODEATLAS_TEMP_DIR`.
- `DELETE /api/session/{id}` removes the cloned repository and every derived artifact.
- Repository content and secrets are never written to application logs.
- The only external calls are to GitHub itself: the clone, plus a metadata-only
  size pre-check (repository name only, never content) that is skipped gracefully
  when offline.

## Configuration

All settings can be overridden via `CODEATLAS_*` environment variables (see
`backend/config.py`): `CODEATLAS_TEMP_DIR`, `CODEATLAS_MAX_REPO_SIZE_MB`,
`CODEATLAS_MAX_FILE_SIZE_BYTES`, `CODEATLAS_CLONE_DEPTH`, ...

## Limitations (current)

- Public GitHub repositories only (HTTPS, no SSH/private repos).
- Python-first analysis; other languages are inventoried but not yet parsed.
- Long path support inside cloned repos is enabled for git itself
  (`core.longpaths`); scanning them additionally benefits from Windows
  long-path support being enabled.
- Later phases (RAG, security scanning, frontend) are not implemented yet.

Ollama/Qwen, Semgrep and Gitleaks setup instructions will be added in the phases
that introduce them.
