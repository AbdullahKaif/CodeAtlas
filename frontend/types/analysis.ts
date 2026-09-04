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
