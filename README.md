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
| - | Frontend dashboard (landing, overview, privacy settings) | Done |
| 2a | Tree-sitter parsing, entities, `contains`/`imports` relationships | Done |
| 2b | `inherits` and `calls` relationships | Done |
| 3a | Semantic chunking (chunks.json) | Done |
| 3b | Embeddings (bge-small) + FAISS + retrieval endpoint | Done |
| 3c | Background analysis with staged progress | Done |
| 4 | Ollama + Qwen3-Coder RAG chat with validated source references | Done |
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

> First analysis on a fresh machine downloads the embedding model
> (`BAAI/bge-small-en-v1.5`, ~130 MB) from HuggingFace; afterwards everything
> runs offline. If the model cannot load, analysis still completes - only
> `/api/search` is unavailable (`index_error` in the response says why).

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements-dev.txt
```

## Local LLM setup (Ollama)

AI chat runs on a local model through [Ollama](https://ollama.com); no API key,
nothing leaves the machine. Install Ollama, start it, and pull the model:

```bash
ollama serve                # if it is not already running as a service
ollama pull qwen3-coder     # default model (~19 GB; needs roughly 24 GB RAM)
```

On a smaller machine pick a lighter coding model and point CodeAtlas at it:

```bash
ollama pull qwen2.5-coder:7b
# then, before starting the backend:
export OLLAMA_MODEL=qwen2.5-coder:7b      # Windows: set OLLAMA_MODEL=qwen2.5-coder:7b
```

Without Ollama everything else still works; the chat page shows the setup
steps and `GET /api/llm/health` reports what is missing.

## Frontend setup

Requires Node 18+.

```bash
cd frontend
npm install
```

## Run CodeAtlas

Terminal 1 - backend API:

```bash
uvicorn backend.main:app --reload
```

Terminal 2 - frontend:

```bash
cd frontend
npm run dev
```

Open http://localhost:3000, paste a GitHub URL and hit **Analyze**.
(The raw API docs remain at http://127.0.0.1:8000/docs. If the backend runs on a
different host/port, set `NEXT_PUBLIC_API_URL` - see `frontend/.env.local.example`.)

### Current endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/analyze` | Start a background analysis in an isolated session (202 + session id) |
| GET | `/api/analysis/{session_id}/status` | Real per-stage progress (cloning ... embedding n/m chunks) |
| POST | `/api/search` | Retrieve the chunks most similar to a question (local embeddings) |
| POST | `/api/chat` | Answer a question with the local LLM, grounded in retrieved code; citations validated |
| GET | `/api/llm/health` | Whether Ollama and the configured model are ready (setup instructions if not) |
| GET | `/api/repository/{session_id}/overview` | Read back the analysis result |
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
- LLM inference goes to the local Ollama server only (`OLLAMA_BASE_URL`,
  default `http://127.0.0.1:11434`). Prompts and answers are never logged.

## How AI answers stay honest

- The model only ever sees the top-k retrieved chunks that fit a fixed context
  budget (`CODEATLAS_LLM_CONTEXT_MAX_CHARS`), never the whole repository.
- The system prompt requires a `Sources:` list in a fixed `file: lines a-b`
  format. Every reference is validated against the analysis: the file must be
  one the model was shown, the line range must overlap a retrieved chunk of
  that file, and a `::Symbol` must exist in that file. Invalid references are
  removed, never repaired, and the response reports how many were dropped.
- Each answer returns the retrieved chunks it was based on (`context`), so the
  UI can show the evidence next to the answer.

## Configuration

All settings can be overridden via `CODEATLAS_*` environment variables (see
`backend/config.py`): `CODEATLAS_TEMP_DIR`, `CODEATLAS_MAX_REPO_SIZE_MB` (0 = unlimited, the default),
`CODEATLAS_MAX_FILE_SIZE_BYTES`, `CODEATLAS_CLONE_DEPTH`, `CODEATLAS_EMBEDDING_MODEL`,
`CODEATLAS_TOP_K`, ...

The LLM settings also accept the bare names from the spec: `OLLAMA_BASE_URL`,
`OLLAMA_MODEL`, `LLM_TIMEOUT` (seconds, default 180). Related knobs:
`CODEATLAS_LLM_CONTEXT_MAX_CHARS`, `CODEATLAS_LLM_NUM_CTX`,
`CODEATLAS_LLM_TEMPERATURE`, `CODEATLAS_CHAT_HISTORY_TURNS`.

## Limitations (current)

- Public GitHub repositories only (HTTPS, no SSH/private repos).
- Python-first analysis; other languages are inventoried but not yet parsed.
- `inherits` and `calls` edges are static best-effort: they only link names
  that resolve to entities inside the repository (see `docs/adr/0001`).
  Inherited-method calls (`self.x()` defined on a base class), dynamic dispatch
  and computed callees are deliberately not edges.
- Long path support inside cloned repos is enabled for git itself
  (`core.longpaths`); scanning them additionally benefits from Windows
  long-path support being enabled.
- Chat answers are only as good as retrieval: a question about code that was
  not retrieved gets an honest "the retrieved code does not show..." rather
  than a guess.
- Security scanning, the architecture graph, impact analysis and onboarding
  are not implemented yet.

Semgrep and Gitleaks setup instructions will be added in the phase that
introduces them.
