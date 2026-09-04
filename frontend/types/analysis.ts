/** Mirrors the backend Pydantic models (backend/repository/scanner.py, api/analyze.py). */

export interface FileInfo {
  path: string;
  name: string;
  extension: string;
  language: string | null;
  size_bytes: number;
  line_count: number | null;
  is_entry_point: boolean;
  is_project_file: boolean;
  is_test_file: boolean;
}

export interface ScanSummary {
  total_files_seen: number;
  files_included: number;
  files_skipped_binary: number;
  files_skipped_large: number;
  files_skipped_other: number;
  dirs_skipped: number;
  truncated: boolean;
}

export interface RepositoryScan {
  root: string;
  files: FileInfo[];
  languages: Record<string, number>;
  entry_points: string[];
  project_files: string[];
  total_size_bytes: number;
  summary: ScanSummary;
}

export interface ParseSummary {
  python_files: number;
  files_parsed: number;
  files_failed: number;
  files_skipped_large: number;
  files_with_syntax_errors: number;
  failed_files: string[];
  entities: Record<string, number>; // count per entity type (file/class/function/method)
  relationships: Record<string, number>; // count per relation (contains/imports/...)
}

export interface ChunkSummary {
  total: number;
  by_type: Record<string, number>; // count per chunk type (function/method/class/module/...)
  files_chunked: number;
  oversized_split: number;
}

export interface IndexSummary {
  chunks_indexed: number;
  dimension: number;
  model: string;
}

export type StageState = "pending" | "running" | "completed" | "failed";

export interface StageStatus {
  name: string; // cloning | scanning | parsing | chunking | embedding | indexing
  state: StageState;
  detail?: string | null; // e.g. "1200/4000 chunks"
}

export interface AnalysisStatus {
  session_id: string;
  state: "running" | "completed" | "failed";
  stages: StageStatus[];
  error?: string | null;
  started_at: string;
  finished_at?: string | null;
  repository: Record<string, string>;
}

/** POST /api/analyze now returns immediately; poll AnalysisStatus for progress. */
export interface AnalyzeStarted {
  session_id: string;
  repository: { name: string; url: string };
  state: "running";
}

export interface AnalyzeResponse {
  session_id: string;
  repository: { name: string; url: string };
  scan: RepositoryScan;
  /** Absent only for sessions persisted before the respective stage existed. */
  parse?: ParseSummary | null;
  chunks?: ChunkSummary | null;
  /** null when embedding failed; index_error then explains why. */
  index?: IndexSummary | null;
  index_error?: string | null;
  /** null when no scanner could run; scanner statuses say which tools ran. */
  security?: SecurityOverview | null;
  security_error?: string | null;
}

/** A past analysis remembered in localStorage (client-side only, no code stored). */
export interface RecentSession {
  sessionId: string;
  name: string;
  url: string;
  analyzedAt: string;
}

/* ---- AI chat (backend/api/chat.py, backend/rag/pipeline.py) ---- */

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/** A citation that survived validation against the knowledge base. */
export interface SourceReference {
  file: string;
  start_line?: number | null;
  end_line?: number | null;
  symbol?: string | null;
  chunk_id?: string | null;
}

/** A retrieved chunk shown to the model (the evidence behind an answer). */
export interface RetrievedChunk {
  chunk_id: string;
  file: string;
  symbol?: string | null;
  entity_id?: string | null;
  type: string;
  start_line: number;
  end_line: number;
  part?: number | null;
  text: string;
  score: number;
}

export interface ChatAnswer {
  session_id: string;
  question: string;
  answer: string;
  sources: SourceReference[];
  context: RetrievedChunk[];
  references_removed: number;
  model: string;
  duration_seconds: number;
}

export interface LLMHealth {
  reachable: boolean;
  base_url: string;
  model: string;
  model_available: boolean;
  available_models: string[];
  ready: boolean;
  message: string;
}

/* ---- Security (backend/security/models.py, backend/security/explain.py) ---- */

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO";
export type ScannerName = "semgrep" | "gitleaks";

export interface Finding {
  id: string;
  fingerprint: string;
  severity: Severity;
  category: "vulnerability" | "secret";
  type: string;
  file: string;
  line: number;
  end_line?: number | null;
  column?: number | null;
  end_column?: number | null;
  source: ScannerName;
  rule: string;
  message: string;
  code_context?: string | null;
  cwe: string[];
  owasp: string[];
  references: string[];
}

export interface ScannerStatus {
  name: ScannerName;
  available: boolean;
  version?: string | null;
  ran: boolean;
  findings: number;
  duration_seconds?: number | null;
  error?: string | null;
  install_hint?: string | null;
}

export interface SecuritySummary {
  total: number;
  by_severity: Record<string, number>;
  vulnerabilities: number;
  secrets: number;
}

export interface SecurityReport {
  session_id: string;
  scanned_at: string;
  scanners: ScannerStatus[];
  summary: SecuritySummary;
  findings: Finding[];
  truncated: boolean;
}

export interface SecurityOverview {
  summary: SecuritySummary;
  scanners: ScannerStatus[];
}

export interface SecurityExplanation {
  finding: Finding;
  explanation: string;
  sources: SourceReference[];
  context: RetrievedChunk[];
  references_removed: number;
  model: string;
  cached: boolean;
  generated_at: string;
  duration_seconds: number;
}

export interface SecurityFix {
  finding: Finding;
  explanation: string;
  suggested_code: string;
  diff: string;
  side_effects: string;
  region_start_line: number;
  region_end_line: number;
  disclaimer: string;
  model: string;
  cached: boolean;
  generated_at: string;
  duration_seconds: number;
}

/* ---- Architecture graph (backend/architecture/graph.py) ---- */

export interface GraphNode {
  id: string;
  label: string;
  type: "file" | "class" | "function" | "method";
  file: string;
  package: string;
  language?: string | null;
  is_test: boolean;
  is_entry_point: boolean;
  start_line?: number | null;
  end_line?: number | null;
  docstring?: string | null;
  classes: number;
  functions: number;
  degree: number;
}

export type Relation = "imports" | "calls" | "inherits" | "contains";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: Relation;
  count: number;
}

export interface GraphStats {
  level: "file" | "entity";
  total_nodes: number;
  total_edges: number;
  shown_nodes: number;
  shown_edges: number;
  truncated: boolean;
  focus?: string | null;
  depth?: number | null;
}

export interface ArchitectureGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  packages: string[];
  stats: GraphStats;
  note: string;
}

/* ---- Entity search (backend/api/repository.py) ---- */

export interface EntitySummary {
  id: string;
  type: string;
  name: string;
  file: string;
  start_line: number;
  end_line: number;
  parent?: string | null;
  signature?: string | null;
  docstring?: string | null;
  dependents: number;
}

export interface EntitySearchResponse {
  session_id: string;
  query: string;
  results: EntitySummary[];
  total_entities: number;
}

/* ---- Impact analysis (backend/impact) ---- */

export type ImpactLevel = "HIGH" | "MEDIUM" | "LOW";

export interface ImpactTarget {
  id: string;
  type: string;
  name: string;
  file: string;
  start_line: number;
  end_line: number;
  signature?: string | null;
  docstring?: string | null;
  members: number;
}

export interface AffectedEntity {
  id: string;
  type: string;
  name: string;
  file: string;
  start_line: number;
  via: "calls" | "imports" | "inherits" | "member";
  through: string;
  depth: number;
  line?: number | null;
  is_test: boolean;
}

export interface ImpactResult {
  target: ImpactTarget;
  level: ImpactLevel;
  reasons: string[];
  affected: AffectedEntity[];
  files: string[];
  tests: string[];
  counts: {
    callers: number;
    importers: number;
    subclasses: number;
    transitive: number;
    files: number;
    tests: number;
  };
  depth: number;
  truncated: boolean;
  note: string;
}

export interface ImpactExplanation {
  target: string;
  depth: number;
  explanation: string;
  sources: SourceReference[];
  context: RetrievedChunk[];
  references_removed: number;
  model: string;
  cached: boolean;
  generated_at: string;
  duration_seconds: number;
  note: string;
}

/* ---- Onboarding (backend/onboarding) ---- */

export interface FileRecommendation {
  path: string;
  reasons: string[];
  score: number;
  symbols: string[];
}

export interface KeyConcept {
  name: string;
  kind: string;
  file?: string | null;
  entity_id?: string | null;
  summary?: string | null;
}

export interface ReadingStep {
  order: number;
  path: string;
  why: string;
  symbols: string[];
}

export interface OnboardingStage {
  number: string;
  title: string;
  detected: boolean;
  explanation: string;
  files: string[];
  symbols: string[];
  questions: string[];
}

export interface LearningDay {
  day: number;
  theme: string;
  files: string[];
  goal: string;
}

export interface OnboardingGuide {
  repository: string;
  overview: {
    name: string;
    description?: string | null;
    description_source?: string | null;
    languages: Record<string, number>;
    source_files: number;
    classes: number;
    functions: number;
    entry_points: string[];
    test_files: number;
    project_files: string[];
    security?: { total: number; critical: number; high: number; secrets: number } | null;
  };
  architecture: {
    packages: { name: string; files: number; classes: number; functions: number }[];
    hubs: { file: string; imported_by: number; imports: number }[];
    relationship_counts: Record<string, number>;
  };
  important_files: FileRecommendation[];
  reading_order: ReadingStep[];
  key_concepts: KeyConcept[];
  stages: OnboardingStage[];
  learning_path: LearningDay[];
  note: string;
}

export interface RepositorySummary {
  summary: string;
  sources: SourceReference[];
  context: RetrievedChunk[];
  references_removed: number;
  model: string;
  cached: boolean;
  generated_at: string;
}
