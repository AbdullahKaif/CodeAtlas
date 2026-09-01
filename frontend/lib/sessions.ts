import type { AnalyzeResponse, RecentSession } from "@/types/analysis";

const KEY = "codeatlas.recentSessions";
const OVERVIEW_KEY = (id: string) => `codeatlas.overview.${id}`;

/** localStorage helpers - metadata only, repository content is never stored here. */

export function rememberSession(result: AnalyzeResponse): void {
  try {
    const entry: RecentSession = {
      sessionId: result.session_id,
      name: result.repository.name,
      url: result.repository.url,
      analyzedAt: new Date().toISOString(),
    };
    const rest = listSessions().filter((s) => s.sessionId !== entry.sessionId);
    localStorage.setItem(KEY, JSON.stringify([entry, ...rest].slice(0, 8)));
    sessionStorage.setItem(OVERVIEW_KEY(result.session_id), JSON.stringify(result));
  } catch {
    /* storage full or unavailable - non-fatal */
  }
}

export function listSessions(): RecentSession[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as RecentSession[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function forgetSession(sessionId: string): void {
  try {
    localStorage.setItem(
      KEY,
      JSON.stringify(listSessions().filter((s) => s.sessionId !== sessionId)),
    );
    sessionStorage.removeItem(OVERVIEW_KEY(sessionId));
  } catch {
    /* ignore */
  }
}

export function cachedOverview(sessionId: string): AnalyzeResponse | null {
  try {
    const raw = sessionStorage.getItem(OVERVIEW_KEY(sessionId));
    return raw ? (JSON.parse(raw) as AnalyzeResponse) : null;
  } catch {
    return null;
  }
}
