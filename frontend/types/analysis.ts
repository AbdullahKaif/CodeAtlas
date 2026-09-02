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

export interface AnalyzeResponse {
  session_id: string;
  repository: { name: string; url: string };
  scan: RepositoryScan;
  /** Absent only for sessions persisted before the respective stage existed. */
  parse?: ParseSummary | null;
  chunks?: ChunkSummary | null;
}

/** A past analysis remembered in localStorage (client-side only, no code stored). */
export interface RecentSession {
  sessionId: string;
  name: string;
  url: string;
  analyzedAt: string;
}
