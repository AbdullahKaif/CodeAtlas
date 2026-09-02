# CodeAtlas — Complete Product & Engineering Specification

## 1. Project Overview

**CodeAtlas** is a local-first, AI-powered code intelligence and security platform for understanding unfamiliar GitHub repositories.

It is designed for developers joining existing projects, working with third-party/open-source repositories, onboarding engineers, and reviewing unfamiliar code before using it.

### Core promise

> **Understand. Secure. Navigate. Onboard.**

A user provides a GitHub repository URL. CodeAtlas clones it locally, scans and parses its structure, builds a searchable repository knowledge base, detects security vulnerabilities and exposed secrets, and uses a local coding LLM to answer questions grounded in the actual repository.

The system should prioritize accuracy, evidence, privacy, local processing, security, developer experience, and reliable demo performance.

---

## 2. Problem

Developers often receive unfamiliar repositories with little understanding of their architecture, dependencies, data flow, security posture, or important components. Manually reading thousands of lines of code is slow and error-prone.

CodeAtlas combines repository understanding, deterministic security scanning, architecture visualization, impact analysis, AI-powered explanation, and developer onboarding into one tool.

---

## 3. End-to-End Solution

```text
GitHub URL
    ↓
GitPython
    ↓
Repository Scanner
    ↓
Tree-sitter Parser
    ↓
Entities + Relationships
    ↓
Repository Knowledge Base
    ↓
Semantic Code Chunking
    ↓
Local Embedding Model
    ↓
FAISS Vector Index
    ↓
RAG Retrieval
    ↓
Qwen3-Coder via Ollama
    ↓
AI Repository Intelligence
```

Security runs in parallel:

```text
Cloned Repository
    ↓
Semgrep ───────→ Vulnerability Findings
    ↓
Gitleaks ──────→ Secret Findings
    ↓
Unified Security Engine
    ↓
AI Explanation + Remediation
```

Repository intelligence also powers:

```text
Entities + Relationships
        ↓
Architecture Graph
        ↓
Impact Analysis
        ↓
Onboarding / Documentation
```

---

## 4. Primary User Flow

1. User opens CodeAtlas.
2. User pastes a GitHub repository URL.
3. User clicks **Analyze Repository**.
4. CodeAtlas creates a unique temporary session.
5. GitPython clones the repository locally.
6. The scanner identifies relevant files.
7. Tree-sitter parses supported source files.
8. CodeAtlas extracts files, classes, functions, methods, imports, and relationships.
9. A repository knowledge base is created.
10. Code is semantically chunked.
11. A local embedding model generates vectors.
12. FAISS indexes the vectors.
13. Semgrep and Gitleaks scan the repository.
14. Qwen3-Coder is made available through Ollama.
15. The dashboard presents repository overview, security, architecture, AI chat, impact analysis, and onboarding.
16. Users can ask repository-specific questions.
17. Answers are grounded in retrieved code and include source references.
18. Users can inspect vulnerabilities and request AI explanations/fix suggestions.
19. Users can explore architecture and impact.
20. Users can use New Developer Mode.
21. Users can delete the entire analysis session.

---

# 5. Technology Stack

## Backend

- Python 3.11+
- FastAPI
- Uvicorn
- Pydantic
- GitPython
- Tree-sitter
- Sentence Transformers
- FAISS CPU
- Ollama
- Qwen3-Coder
- Semgrep
- Gitleaks

## Frontend

- Next.js
- React
- TypeScript
- Tailwind CSS
- React Flow

Use additional libraries only when genuinely useful. Avoid unnecessary infrastructure.

---

# 6. Architecture

Recommended structure:

```text
codeatlas/
│
├── backend/
│   ├── main.py
│   ├── config.py
│   │
│   ├── api/
│   │   ├── analyze.py
│   │   ├── repository.py
│   │   ├── chat.py
│   │   ├── security.py
│   │   ├── architecture.py
│   │   ├── impact.py
│   │   ├── onboarding.py
│   │   ├── documentation.py
│   │   └── sessions.py
│   │
│   ├── repository/
│   │   ├── clone.py
│   │   ├── scanner.py
│   │   └── metadata.py
│   │
│   ├── parser/
│   │   ├── tree_parser.py
│   │   ├── models.py
│   │   ├── entities.py
│   │   └── relationships.py
│   │
│   ├── knowledge/
│   │   ├── builder.py
│   │   └── serializer.py
│   │
│   ├── rag/
│   │   ├── chunker.py
│   │   ├── embeddings.py
│   │   ├── vector_store.py
│   │   ├── retriever.py
│   │   ├── pipeline.py
│   │   └── prompts.py
│   │
│   ├── llm/
│   │   └── ollama_client.py
│   │
│   ├── security/
│   │   ├── semgrep.py
│   │   ├── gitleaks.py
│   │   ├── engine.py
│   │   ├── models.py
│   │   └── normalizer.py
│   │
│   ├── architecture/
│   │   └── graph.py
│   │
│   ├── impact/
│   │   └── analyzer.py
│   │
│   ├── onboarding/
│   │   └── generator.py
│   │
│   ├── documentation/
│   │   └── generator.py
│   │
│   └── tests/
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   ├── types/
│   └── hooks/
│
├── fixtures/
│   └── demo-repository/
│
├── docs/
├── README.md
├── DEMO.md
└── .gitignore
```

The exact structure may change if Codex finds a cleaner design, but responsibilities must remain modular.

---

# 7. Repository Ingestion

Use GitPython to:

- Validate a GitHub HTTPS URL.
- Generate a unique session ID.
- Create a temporary directory.
- Clone the repository.
- Return repository metadata.
- Handle clone failures.
- Never execute repository code.

Example:

```text
temp/
└── session_<uuid>/
    ├── repository/
    ├── analysis/
    ├── vectors/
    └── security/
```

Public repositories are the MVP target.

---

# 8. Repository Scanner

Recursively identify relevant source files.

Ignore at minimum:

```text
.git
node_modules
.venv
venv
__pycache__
dist
build
coverage
.idea
.vscode
.cache
```

Also ignore binaries, media, archives, generated artifacts, and excessively large files.

Detect useful project files:

```text
README.md
requirements.txt
pyproject.toml
package.json
Dockerfile
configuration files
test directories
```

For each source file collect:

```json
{
  "path": "app/auth.py",
  "language": "python",
  "extension": ".py",
  "size": 4120
}
```

Use configurable file-size and repository-size limits.

---

# 9. Tree-sitter Parser

Tree-sitter is the structural parsing engine.

Initially prioritize Python while keeping the parser extensible for JavaScript, TypeScript, Java, C++, Go, Rust, and other languages later.

Extract:

- Classes
- Functions
- Methods
- Imports
- Parameters
- Decorators
- Docstrings
- Start/end line numbers
- Parent classes
- Signatures
- Source code

Example:

```json
{
  "id": "app/auth.py::AuthService.login",
  "type": "method",
  "name": "login",
  "file": "app/auth.py",
  "parent_class": "AuthService",
  "start_line": 20,
  "end_line": 45,
  "parameters": ["username", "password"],
  "docstring": "Authenticate a user.",
  "source_code": "..."
}
```

A broken file should not crash the complete analysis.

---

# 10. Entities

Primary entities:

```text
file
class
function
method
module
```

Useful fields:

```text
id
type
name
file
start_line
end_line
parameters
signature
docstring
source_code
parent
language
```

Do not extract every local variable by default.

---

# 11. Relationships

Build a lightweight static graph.

Support:

```text
imports
calls
inherits
contains
defines
```

Example:

```json
{
  "source": "routes/auth.py",
  "relation": "imports",
  "target": "app/auth.py"
}
```

Only report calls when statically detectable with reasonable confidence. Never claim perfect runtime dependency analysis.

---

# 12. Knowledge Base

Create temporary structured files:

```text
repository.json
entities.json
relationships.json
dependencies.json
chunks.json
```

The original repository remains the source of truth.

The knowledge base is an analysis representation, not a replacement for source code.

---

# 13. Semantic Chunking

RAG chunks should be meaningful.

Prefer:

```text
Function
Method
Class
Module-level code
Important documentation/configuration
```

Each chunk should include:

```json
{
  "chunk_id": "chunk_001",
  "file": "app/auth.py",
  "symbol": "login",
  "type": "function",
  "start_line": 20,
  "end_line": 45,
  "text": "..."
}
```

Large entities can be split recursively while preserving metadata.

Avoid excessive duplicate chunks.

---

# 14. Embeddings

Create an embedding abstraction.

Initial model:

```text
BAAI/bge-small-en-v1.5
```

Use Sentence Transformers.

Requirements:
- Batch embedding
- Configurable model
- CPU compatibility
- Easy model switching
- Proper vector normalization/configuration
- Clear model-loading errors

The model must not be hard-coded throughout the application.

---

# 15. Embedding Evaluation

Support comparison of multiple local embedding models.

Create a repository-specific evaluation set:

```text
Question → Expected relevant code
```

Example:

```text
Where is authentication implemented?
How are database connections created?
Where is user registration handled?
Which module handles API requests?
Where is JWT validation performed?
```

Measure:

- Recall@K
- MRR
- Precision@K where useful

Choose the production model based on retrieval quality and practical performance.

---

# 16. FAISS

Use FAISS CPU for local vector search.

Implement:

- Build index
- Add vectors
- Save index
- Load index
- Search top-k
- Delete index
- Metadata mapping

Never hard-code vector dimension.

Example:

```text
vectors/
├── index.faiss
└── metadata.json
```

---

# 17. Retrieval

Pipeline:

```text
User Question
      ↓
Embedding
      ↓
FAISS
      ↓
Top-K chunks
      ↓
Optional filtering/reranking
      ↓
Context
      ↓
LLM
```

Return source metadata:

```json
{
  "chunk_id": "chunk_001",
  "file": "app/auth.py",
  "symbol": "login",
  "start_line": 20,
  "end_line": 45,
  "score": 0.82
}
```

Never send the entire repository to the LLM.

---

# 18. Ollama + Qwen3-Coder

Use Ollama for local LLM inference.

Default model:

```text
Qwen3-Coder
```

Create a clean `LLMClient` abstraction with:

```text
health_check()
model_available()
generate()
```

Configuration:

```text
OLLAMA_BASE_URL
OLLAMA_MODEL
```

If Ollama is unavailable, return a clear setup message rather than crashing.

Do not require paid AI APIs.

---

# 19. RAG

End-to-end:

```text
Question
→ Embed
→ FAISS
→ Retrieve
→ Build Context
→ Qwen3-Coder
→ Answer
→ Validate Sources
```

Qwen should:
- Use repository context.
- Avoid inventing repository facts.
- Say when evidence is insufficient.
- Explain clearly.
- Cite relevant files/functions/lines.

Example:

```text
Authentication is handled primarily by AuthService.

The login flow begins in routes/auth.py and calls
AuthService.login() in app/auth.py.

Sources:
- routes/auth.py: lines 10–28
- app/auth.py: lines 20–45
```

---

# 20. Source Reference Validation

Never blindly trust LLM-generated citations.

Validate that:
- File exists.
- Symbol exists when available.
- Line range corresponds to retrieved context.

Invalid references must be removed.

CodeAtlas metadata is authoritative.

---

# 21. Security Engine

Use:

### Semgrep

For:
- Static vulnerability detection
- Unsafe patterns
- Security rules

### Gitleaks

For:
- Exposed secrets
- API keys
- Tokens
- Credentials

Never expose actual secret values in logs, UI, or API responses.

---

# 22. Unified Security Model

Normalize scanner findings:

```json
{
  "id": "SEC-001",
  "severity": "HIGH",
  "type": "SQL Injection",
  "file": "app/database.py",
  "line": 42,
  "source": "Semgrep",
  "rule": "...",
  "message": "...",
  "code_context": "..."
}
```

Security findings must originate from deterministic scanner results.

AI should explain findings rather than invent them.

---

# 23. AI Security Explanation

For each finding:

```text
Security Finding
      ↓
Retrieve Vulnerable Code
      ↓
Retrieve Related Context
      ↓
Qwen3-Coder
      ↓
Explanation
      ↓
Impact
      ↓
Remediation
```

Explain:
- What is wrong?
- Why it matters
- Potential impact
- Relevant data flow
- Recommended remediation

Use careful language such as:

> The scanner detected...

> This code appears vulnerable because...

> Potential impact includes...

Do not claim exploitability without sufficient evidence.

---

# 24. AI Fix Suggestions

Return:

```text
Explanation
Suggested code
Unified diff
Potential side effects
```

Never automatically modify the repository.

Clearly label:

> AI-generated suggestion — review before applying.

---

# 25. Architecture Visualization

Use React Flow.

Backend returns:

```json
{
  "nodes": [],
  "edges": []
}
```

Nodes may represent:
- Modules
- Files
- Important classes
- Important components

Edges:
- imports
- calls
- inheritance
- containment

Support:
- Zoom
- Pan
- Search
- Filtering
- Node selection
- Metadata inspection

Do not render huge graphs in a way that freezes the browser.

---

# 26. Impact Analysis

Allow selection of:

```text
File
Class
Function
Method
```

Identify:

```text
Callers
Importers
Dependents
Related classes
Tests
```

Then optionally use RAG + Qwen to explain likely consequences.

Example:

```text
Changing AuthService.validate_token()
may affect:

routes/auth.py
middleware/auth.py
tests/test_auth.py
AdminService
```

Clearly label results as:

> Static / AI-assisted impact analysis

---

# 27. Developer Onboarding

Generate:

### Project overview

What the project does.

### Architecture

Major modules and relationships.

### Important files

What matters and why.

### Reading order

What to inspect first.

### Key concepts

Important technical/domain concepts.

### Learning path

Example:

```text
Day 1 — Repository structure
Day 2 — Authentication
Day 3 — Business logic
Day 4 — Database
Day 5 — Testing
```

Recommendations must reference actual repository files.

---

# 28. Documentation Generation

Support:
- README draft
- Architecture documentation
- Developer guide
- API overview

Use repository evidence and RAG.

Return Markdown.

Never overwrite existing documentation automatically.

---

# 29. Test Generation

Allow selecting a function/method.

Retrieve:
- Function code
- Related code
- Existing tests
- Project testing conventions

Generate suggested tests for:
- Normal cases
- Edge cases
- Invalid inputs
- Error conditions

Return suggestions only.

---

# 30. Codebase Health

Use deterministic indicators.

### Security

- High findings
- Medium findings
- Secret findings

### Documentation

- README presence
- Documentation files
- Docstring coverage where measurable

### Maintainability

- Very large files
- Very large functions
- Basic complexity indicators

### Dependencies

- Dependency files
- Dependency count
- Other reliable local indicators

Call these:

> Codebase Health Indicators

Do not claim they are standardized industry scores.

---

# 31. Frontend

Use Next.js, TypeScript, and Tailwind.

Navigation:

```text
Overview
Security
AI Chat
Architecture
Impact Analysis
Onboarding
Documentation
Settings
```

Landing page:

```text
CODEATLAS

Understand. Secure. Navigate.

[ GitHub Repository URL                 ]

[ Analyze Repository ]
```

The interface should look like a serious developer/security product, not a generic chatbot.

---

# 32. Overview Dashboard

Display:

- Repository name
- Languages
- Frameworks
- File count
- Classes
- Functions
- Dependencies
- Security summary
- Codebase health indicators
- Important modules
- AI repository summary

Never fabricate values.

---

# 33. Security Dashboard

Show:

```text
Critical
High
Medium
Low
Secrets
```

Each finding should show:

```text
Severity
Type
Rule
File
Line
Scanner
```

Detail view:

```text
Finding
↓
Vulnerable Code
↓
Explanation
↓
Impact
↓
Potential Data Flow
↓
Suggested Fix
```

Never display secret values.

---

# 34. AI Chat

Create a repository-aware chat interface.

Example questions:

```text
How does authentication work?
Where is the database connection initialized?
What happens when a user logs in?
Which files handle API requests?
Explain the project architecture.
What would break if I change AuthService?
Where are the main security risks?
```

Answers should contain source references whenever possible.

---

# 35. Architecture UI

Interactive graph with:

- Zoom
- Pan
- Search
- Filters
- Node details
- Relationship details
- Source navigation

Limit initial graph scope for large repositories.

---

# 36. Impact UI

Display:

```text
Selected Component

Impact: HIGH

Potentially affected:
- file A
- function B
- class C
- test D

Reason:
...
```

Include evidence and uncertainty.

---

# 37. Onboarding UI

Present a guided sequence:

```text
01 — Understand the project
02 — Learn the architecture
03 — Understand authentication
04 — Understand business logic
05 — Understand persistence
06 — Understand testing
```

Each stage includes:
- Explanation
- Files
- Symbols
- Questions to ask

---

# 38. Privacy

CodeAtlas is local-first.

Default:
- Repository processing is local.
- LLM inference is local.
- Embeddings are local.
- FAISS is local.
- Security scanning is local.
- Temporary session storage is used.

Never upload repository source to external AI APIs by default.

Provide:

```text
Delete Session Data
```

Deletion must remove:
- Repository
- Metadata
- Chunks
- Embeddings
- FAISS index
- Security results
- Generated analysis

---

# 39. Security of CodeAtlas

Treat every repository as untrusted.

Never:
- Execute repository code.
- Install dependencies from it automatically.
- Run setup scripts.
- Run arbitrary commands found inside it.
- Trust repository configuration blindly.

Use safe subprocess execution for Semgrep/Gitleaks.

Use:
- Timeouts
- Output limits
- Argument arrays
- No unnecessary shell interpolation
- Path validation
- Session isolation

---

# 40. API

Recommended endpoints:

```text
POST   /api/analyze

GET    /api/repository/{session_id}/overview

POST   /api/chat

GET    /api/security/{session_id}

POST   /api/security/explain

POST   /api/security/fix

GET    /api/architecture/{session_id}

POST   /api/impact

GET    /api/onboarding/{session_id}

POST   /api/documentation

POST   /api/tests

DELETE /api/session/{session_id}

GET    /api/health
```

Use Pydantic request/response models and consistent errors.

---

# 41. Analysis Progress

Expose analysis stages:

```text
Cloning
Scanning
Parsing
Building knowledge base
Chunking
Embedding
Indexing
Security scanning
Finalizing
```

Frontend should show real progress/status.

Do not fake progress.

---

# 42. Error Handling

Gracefully handle:

- Invalid URL
- Inaccessible repository
- Clone failure
- Empty repository
- Huge repository
- Unsupported language
- Parser failure
- Embedding failure
- FAISS failure
- Ollama unavailable
- Model unavailable
- Semgrep unavailable
- Gitleaks unavailable
- LLM timeout
- Session deletion failure

Optional component failure should not necessarily destroy the entire analysis.

---

# 43. Performance

Optimize for normal developer laptops.

Use:
- File filtering
- Size limits
- Batching
- Caching
- Embedding reuse
- FAISS
- Top-k retrieval
- Limited LLM context
- Background processing where useful

Never send the entire repository to Qwen.

Never recompute unchanged embeddings unnecessarily.

---

# 44. Supported Languages

MVP:

```text
Python
```

Architecture should make adding these possible later:

```text
JavaScript
TypeScript
Java
C++
Go
Rust
```

Prefer a reliable Python implementation over several unreliable language implementations.

---

# 45. Testing

Create:

```text
fixtures/demo-repository/
```

It should contain:
- Multiple modules
- Classes
- Functions
- Imports
- Relationships
- Tests
- Safe deliberately vulnerable examples
- Fake non-sensitive secret-like values

Test:

```text
Clone
Scanner
Parser
Entity extraction
Import extraction
Relationships
Knowledge base
Chunking
Embeddings
FAISS
Retrieval
RAG
Security
Architecture
Impact
Onboarding
Deletion
```

Never use real credentials.

---

# 46. Codebase Health and Quality

Use:
- Type hints
- Pydantic models
- Clear module boundaries
- Environment-based configuration
- Safe subprocess handling
- Useful comments
- Unit tests
- Integration tests

Avoid:
- Hard-coded absolute paths
- Unnecessary dependencies
- Fake implementations
- Placeholder security findings
- Hard-coded demo answers
- Over-engineering

---

# 47. Demo Repository

Create or use a small fictional repository suitable for demonstrating:

```text
Authentication
API layer
Service layer
Database layer
Tests
A deliberate vulnerability
A fake secret
Multiple modules
```

The demo repository must contain no real credentials.

---

# 48. Demo Story

The presentation should demonstrate:

### 1. Repository ingestion

Paste a GitHub URL.

### 2. Automatic analysis

Show progress.

### 3. Overview

Show what the repository is, technologies, structure, and health.

### 4. AI understanding

Ask:

> How does authentication work?

Show grounded answer with source references.

### 5. Architecture

Show interactive component relationships.

### 6. Security

Show a detected vulnerability.

### 7. Security explanation

Show:
- Vulnerable code
- Explanation
- Impact
- Potential data flow
- Suggested fix

### 8. Impact analysis

Ask:

> What could be affected if I change this?

### 9. Onboarding

Show a generated developer learning path.

### 10. Privacy

Delete the session and show that temporary data is removed.

---

# 49. Product Differentiators

Emphasize:

## Local-first

Source code remains local.

## Evidence-based AI

AI answers are grounded in retrieved repository code.

## Deterministic security + AI reasoning

Semgrep/Gitleaks detect issues; Qwen explains them.

## Repository graph

Code relationships become an interactive architecture map.

## Impact analysis

Developers can estimate what may be affected before changing code.

## New developer mode

CodeAtlas actively teaches developers how to understand the repository.

---

# 50. MVP Priority

## Tier 1 — Mandatory

1. GitHub cloning
2. Repository scanning
3. Tree-sitter
4. Entity extraction
5. Relationships
6. Semantic chunking
7. Embeddings
8. FAISS
9. Ollama
10. Qwen3-Coder
11. RAG
12. Source references
13. Semgrep
14. Gitleaks
15. Security dashboard
16. AI security explanation
17. Architecture graph
18. Overview dashboard
19. AI chat

## Tier 2 — Differentiators

20. Impact analysis
21. Onboarding
22. AI fix suggestions
23. Codebase health
24. Privacy deletion

## Tier 3 — Nice-to-have

25. Documentation generation
26. Test generation
27. Multi-language expansion
28. Advanced graph analysis
29. Repository comparison

Prioritize reliability over feature count.

---

# 51. Recommended Implementation Order

```text
Project Setup
    ↓
GitPython
    ↓
Repository Scanner
    ↓
Tree-sitter
    ↓
Entity Extraction
    ↓
Relationships
    ↓
Knowledge Base
    ↓
Semantic Chunking
    ↓
Embedding Abstraction
    ↓
Embedding Evaluation
    ↓
FAISS
    ↓
Retriever
    ↓
Ollama
    ↓
RAG
    ↓
Source Validation
    ↓
Semgrep
    ↓
Gitleaks
    ↓
Security Engine
    ↓
AI Security Explanations
    ↓
AI Fix Suggestions
    ↓
Architecture Graph
    ↓
Impact Analysis
    ↓
Repository Overview
    ↓
Onboarding
    ↓
Health Indicators
    ↓
Documentation
    ↓
Test Generation
    ↓
Privacy
    ↓
Frontend Polish
    ↓
Full Testing
    ↓
Demo Preparation
```

---

# 52. Reliability Rules

Never:
- Fabricate repository information.
- Fabricate vulnerabilities.
- Fabricate source references.
- Pretend static analysis is runtime analysis.
- Automatically apply AI code modifications.
- Expose secrets.

Always:
- Prefer repository evidence.
- Show uncertainty.
- Preserve source locations.
- Validate AI-generated references.
- Keep scanner results separate from AI interpretation.

---

# 53. Configuration

Use environment variables/configuration for:

```text
OLLAMA_BASE_URL
OLLAMA_MODEL
EMBEDDING_MODEL
TEMP_DIRECTORY
MAX_FILE_SIZE
MAX_REPOSITORY_SIZE
TOP_K
LLM_TIMEOUT
```

Provide sensible defaults.

---

# 54. Logging

Logs may contain:

```text
session ID
operation
duration
status
error category
```

Never log:
- Source code
- Passwords
- Tokens
- API keys
- Secret values
- Full sensitive prompts

---

# 55. Definition of Done

CodeAtlas is ready when:

### Repository

- GitHub URL works.
- Repository clones successfully.
- Invalid repositories fail gracefully.

### Parsing

- Python files are parsed.
- Functions/classes/methods/imports are extracted.
- Relationships are generated.
- Line numbers are accurate.

### RAG

- Chunks are meaningful.
- Embeddings are local.
- FAISS retrieves relevant code.
- Qwen answers repository questions.
- Sources are validated.

### Security

- Semgrep works.
- Gitleaks works.
- Findings are normalized.
- Secrets are redacted.
- AI explanations work.

### Architecture

- Graph is generated.
- Graph is interactive.
- Nodes can be inspected.

### Impact

- Components can be selected.
- Callers/importers/dependents can be shown.
- AI explanation uses evidence.

### Onboarding

- Overview is generated.
- Important files are identified.
- Reading order is generated.
- Learning path is generated.

### Privacy

- Data remains local by default.
- Session deletion works.
- Repository content is not unnecessarily logged.

### Frontend

- Dashboard works.
- Loading states work.
- Errors are understandable.
- Full demo flow works.

---

# 56. Final Instruction to Codex

Treat this document as the authoritative product and engineering specification for CodeAtlas.

Before making changes:

1. Inspect the existing repository.
2. Identify existing code and working functionality.
3. Do not destroy working components.
4. Create a concise implementation plan.
5. Implement incrementally.
6. Test each subsystem.
7. Fix errors before continuing.
8. Keep modules independent.
9. Prefer simple local technologies.
10. Do not introduce unnecessary infrastructure.
11. Never fabricate functionality.
12. Never fabricate security findings.
13. Never expose secrets.
14. Never execute arbitrary repository code.
15. Never send repository source to external AI APIs by default.

When choosing between another feature and reliability, choose reliability.

When choosing between complicated infrastructure and a simple working design, choose the simple design.

When choosing between flashy UI and accurate repository/security analysis, choose accurate analysis.

The final product should feel like a serious developer-security platform rather than a collection of disconnected AI features.

The most important path is:

```text
GitHub URL
    ↓
Clone
    ↓
Parse
    ↓
Understand
    ↓
Index
    ↓
Retrieve
    ↓
Ask AI
    ↓
Show Evidence
    ↓
Scan Security
    ↓
Explain Risk
    ↓
Visualize Architecture
    ↓
Analyze Impact
    ↓
Onboard Developer
    ↓
Delete Session
```

Build this path reliably first, then expand the product around it.
