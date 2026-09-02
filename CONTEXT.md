# CodeAtlas

Local-first code intelligence: clone a GitHub repository, parse it into a knowledge
base, and answer questions about it with evidence. This glossary is the canonical
vocabulary for all subsystems (parser, RAG, security, graph, impact, onboarding).

## Language

### Analysis

**Session**:
One isolated analysis of one repository, identified by a session ID. All derived
data (clone, knowledge base, vectors, security results) lives and dies with it.
_Avoid_: workspace, project

**Scan**:
The pass that walks a cloned repository and decides which files are analyzable
source files. Produces file metadata only; no parsing.
_Avoid_: index, crawl

**Knowledge base**:
The structured JSON representation of a repository produced by analysis (entities,
relationships, chunks). A derived artifact — the cloned repository remains the
source of truth.
_Avoid_: index, database

### Entities

**Entity**:
A named structural element extracted from source code. Exactly four types exist:
`file`, `class`, `function`, `method`.
_Avoid_: node, symbol, module (a Python file IS a module; `file` is the one term)

**Entity ID**:
The canonical key `<relative POSIX path>::<dotted qualified name>`, e.g.
`app/auth.py::AuthService.login`. A file's ID is its path alone. Nested scopes
use dots (`Outer.inner`). Every subsystem (chunks, vectors, graph, impact,
citations) keys on this.
_Avoid_: any alternative key format

**Method**:
A function defined inside a class body. Its `parent` is the class entity.
_Avoid_: member function

### Relationships

**Relationship**:
A directed edge `{source, relation, target}` between entity IDs, statically
extracted. Exactly four relations exist: `contains`, `imports`, `inherits`,
`calls`.
_Avoid_: defines (synonym of contains; dropped), dependency

**Contains**:
Structural nesting: file contains class/function, class contains method.
_Avoid_: defines, has

**Calls**:
A call edge reported only when the callee resolves by name within the knowledge
base (same-file or imported symbol). Unresolvable calls are omitted, never
guessed. Static best-effort, not runtime truth.

### Retrieval

**Chunk**:
A semantically meaningful span of source (function, method, class, module-level
code, or key documentation) that carries its entity metadata and is the unit of
embedding and retrieval.
_Avoid_: passage, segment

**Source reference**:
A file/symbol/line-range citation attached to an AI answer, validated against
the knowledge base before display. Invalid references are removed, not repaired.
_Avoid_: citation link, source (alone)

### Security

**Finding**:
A normalized security result originating from a deterministic scanner (Semgrep
or Gitleaks). AI explains findings; it never creates them. Secret values are
always redacted.
_Avoid_: vulnerability (a finding may be a secret exposure), issue
