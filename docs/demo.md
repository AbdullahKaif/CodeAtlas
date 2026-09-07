# Demo script

The ten steps of the demo story (specification §48), with what to type, what to
point at, and what each step proves. Budget about twelve minutes.

## Before the demo

1. **Publish the demo repository.** CodeAtlas clones from GitHub, so TaskFlow
   has to live there once:

   ```bash
   cp -r fixtures/demo-repository /tmp/taskflow && cd /tmp/taskflow
   git init -q && git add . && git commit -qm "TaskFlow demo repository"
   gh repo create <your-account>/taskflow-demo --public --source . --push
   ```

   Any small Python repository works too, but TaskFlow is built so that every
   step below has something to show: four layers, tests, four labelled defects
   and synthetic secret-shaped values. It contains no real credentials.

2. **Start everything.**

   ```bash
   ollama serve                       # separate terminal, if not running as a service
   ollama pull qwen3-coder            # once; or qwen2.5-coder:7b on a smaller machine
   pip install semgrep                # once; Gitleaks from its releases page
   uvicorn backend.main:app --reload  # terminal 1
   cd frontend && npm run dev         # terminal 2
   ```

   Open http://localhost:3000. Check the AI Chat page shows "Local model ready"
   before starting; if it shows setup steps, follow them first.

3. **Warm the caches (optional).** Analyze the repository once beforehand and
   click "AI summary" on the Overview and "Explain" on one finding. Cached
   answers return instantly during the demo; the "cached" label stays honest.

## The ten steps

### 1. Repository ingestion

Paste the TaskFlow URL on the landing page and press **Analyze**.

Say: the code is cloned into a temp directory on this machine and never sent
anywhere.

### 2. Automatic analysis

Point at the progress line: cloning, scanning, parsing, chunking, embedding
(with real chunk counts), indexing, security. Nothing here is simulated; the
backend reports each stage as it finishes.

### 3. Overview

On the Overview page:

- **What this repository is**: the description comes from TaskFlow's own README,
  and the framework chips (FastAPI, SQLAlchemy, Pydantic, pytest, Requests) come
  from its dependency files.
- **Codebase Health Indicators**: docstring coverage, oversized files and
  functions, dependency count. Say the word *indicators*: these are counts with
  stated thresholds, not a score.
- **Important modules**: `taskflow/api/routes.py`, `taskflow/auth/service.py`,
  `taskflow/db/repository.py`, each with the structural reason it was chosen.

### 4. AI understanding

Open **AI Chat** and ask:

> How does authentication work?

Expected: an answer naming `AuthService.login`, `hash_password`, and the token
functions, ending with verified sources. Open "Evidence shown to the model" to
show the exact excerpts. Point at the "unverifiable references removed" counter
if it appears: citations the model could not back are dropped, not kept.

Optional second question:

> What happens when a task is completed?

### 5. Architecture

Open **Architecture**. The file graph shows the four layers: `api` imports
`auth` and `services`; `services` calls `db`. Click `taskflow/auth/service.py`
to focus on its entity neighbourhood.

### 6. Security

Open **Security**. Expected findings on TaskFlow:

| Where | What | Scanner |
| --- | --- | --- |
| `taskflow/db/repository.py` | SQL built by string concatenation | Semgrep |
| `taskflow/services/notifications.py` | `subprocess.call` with `shell=True` | Semgrep |
| `taskflow/auth/service.py` | MD5 used for a password | Semgrep |
| `taskflow/config.py` | Secret-shaped values (AWS key id, payment key, password) | Gitleaks / Semgrep |

Point at the secrets: the values are `[REDACTED]` everywhere, including in what
the model is shown.

### 7. Security explanation

Open the SQL injection finding and press **Explain**. The explanation follows
fixed headings: what the scanner detected, why it matters, potential impact,
data flow, remediation. Then **Suggest fix**: a unified diff against the
parameterised form, labelled AI-generated, never applied.

### 8. Impact analysis

Open **Impact Analysis** and pick `taskflow/auth/service.py::AuthService`.

Expected: callers in `taskflow/api/routes.py`, the test in
`tests/test_auth.py`, and a level with its reasons. Press **Explain** for the
AI reading, which cites the dependents it was shown.

Say: static analysis is a lower bound; dynamic dispatch could add dependents it
cannot see, and the page says so.

### 9. Onboarding

Open **Onboarding**. Stages 01 to 06 are all detected for TaskFlow, each with
real files, symbols and suggested questions that link back into the chat. Show
the reading order and the day-by-day learning path.

Optional: open **Documentation**, draft the API overview, and suggest tests for
`taskflow/services/tasks.py::TaskService.create`. Both are downloads; nothing
is written to the repository.

### 10. Privacy

Open **Settings**. "What this session stores" lists the session directory with
each artifact and its size. Press **Delete session data**, confirm, and show the
"Session deleted" page: the backend re-checked and found nothing. Optionally
open the temp directory in a file manager to show the folder is gone.

## If something is missing

- **No Ollama**: every AI button is disabled with the setup steps shown. The
  rest of the demo still works; skip steps 4, 7 and the explanation in 8.
- **No scanners**: the Security page says which tool is missing and how to
  install it. Skip steps 6 and 7.
- **Slow model**: ask the warm-up questions before the demo; cached answers are
  labelled as cached.
