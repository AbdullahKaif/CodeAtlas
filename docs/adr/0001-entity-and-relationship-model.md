# Entity and relationship model deviates from the product spec

The product spec (CodeAtlas_Project_Specification.md §10–11) lists five entity
types (`file, class, function, method, module`) and five relations (`imports,
calls, inherits, contains, defines`). The knowledge base implements four of
each: `module` and `defines` are dropped, and `calls` is constrained. This is
deliberate, not an oversight — every later subsystem (chunking, FAISS metadata,
architecture graph, impact analysis, citation validation) keys on these types
and on the entity ID format `path::Qualified.Name`, so this model is effectively
irreversible once Phase 3 ships.

## Decisions

- **No `module` entity type.** In Python a file IS a module; two types for one
  concept would put duplicate nodes in every downstream consumer. The `file`
  entity carries module-level facts (e.g. the module docstring). If the
  architecture graph later needs package grouping, that is a graph-layer
  concern, not a new entity type.
- **No `defines` relation.** It describes the same structural fact as
  `contains` (file contains class, class contains method). One edge kind, one
  name.
- **`calls` edges (Phase 2 PR 2) only where the callee resolves by name within
  the knowledge base** — same-file or imported-symbol calls. Unresolvable calls
  are omitted, never guessed. This implements the spec's own rule ("only report
  calls when statically detectable with reasonable confidence") as a concrete
  algorithm.
- **`imports` edges only link files inside the repository.** External packages
  are not edges; dependency reporting is a separate later concern
  (dependencies.json).

## Consequences

- The knowledge base never contains an edge or entity it cannot prove from the
  parse tree, which is what makes citation validation (spec §20) trustworthy.
- Anyone comparing code to spec will find fewer types than documented; this ADR
  is the explanation.
