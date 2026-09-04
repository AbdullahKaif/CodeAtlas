import type {
  AnalysisStatus,
  AnalyzeResponse,
  AnalyzeStarted,
  ChatAnswer,
  ChatMessage,
  LLMHealth,
  ArchitectureGraph,
  EntitySearchResponse,
  ImpactExplanation,
  ImpactResult,
  OnboardingGuide,
  RepositorySummary,
  SecurityExplanation,
  SecurityFix,
  SecurityReport,
} from "@/types/analysis";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "Cannot reach the CodeAtlas backend. Is it running? (uvicorn backend.main:app)",
      0,
    );
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export function analyzeRepository(repoUrl: string): Promise<AnalyzeStarted> {
  return request<AnalyzeStarted>("/api/analyze", {
    method: "POST",
    body: JSON.stringify({ repo_url: repoUrl }),
  });
}

export function getAnalysisStatus(sessionId: string): Promise<AnalysisStatus> {
  return request<AnalysisStatus>(
    `/api/analysis/${encodeURIComponent(sessionId)}/status`,
  );
}

export function getOverview(sessionId: string): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>(
    `/api/repository/${encodeURIComponent(sessionId)}/overview`,
  );
}

export function deleteSession(
  sessionId: string,
): Promise<{ session_id: string; deleted: boolean }> {
  return request(`/api/session/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export function getLLMHealth(): Promise<LLMHealth> {
  return request<LLMHealth>("/api/llm/health");
}

export function askQuestion(
  sessionId: string,
  question: string,
  history: ChatMessage[] = [],
): Promise<ChatAnswer> {
  return request<ChatAnswer>("/api/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, question, history }),
  });
}

export function getSecurityReport(sessionId: string): Promise<SecurityReport> {
  return request<SecurityReport>(`/api/security/${encodeURIComponent(sessionId)}`);
}

export function explainFinding(
  sessionId: string,
  findingId: string,
  refresh = false,
): Promise<SecurityExplanation> {
  return request<SecurityExplanation>("/api/security/explain", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, finding_id: findingId, refresh }),
  });
}

export function suggestFix(
  sessionId: string,
  findingId: string,
  refresh = false,
): Promise<SecurityFix> {
  return request<SecurityFix>("/api/security/fix", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, finding_id: findingId, refresh }),
  });
}

export function getArchitecture(
  sessionId: string,
  options: { focus?: string; depth?: number; maxNodes?: number } = {},
): Promise<ArchitectureGraph> {
  const params = new URLSearchParams();
  if (options.focus) params.set("focus", options.focus);
  if (options.depth) params.set("depth", String(options.depth));
  if (options.maxNodes) params.set("max_nodes", String(options.maxNodes));
  const query = params.toString();
  return request<ArchitectureGraph>(
    `/api/architecture/${encodeURIComponent(sessionId)}${query ? `?${query}` : ""}`,
  );
}

export function searchEntities(
  sessionId: string,
  q: string,
  options: { limit?: number; types?: string[] } = {},
): Promise<EntitySearchResponse> {
  const params = new URLSearchParams({ q });
  if (options.limit) params.set("limit", String(options.limit));
  if (options.types?.length) params.set("types", options.types.join(","));
  return request<EntitySearchResponse>(
    `/api/repository/${encodeURIComponent(sessionId)}/entities?${params.toString()}`,
  );
}

export function analyzeImpact(sessionId: string, target: string, depth = 2): Promise<ImpactResult> {
  return request<ImpactResult>("/api/impact", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, target, depth }),
  });
}

export function explainImpact(
  sessionId: string,
  target: string,
  depth = 2,
  refresh = false,
): Promise<ImpactExplanation> {
  return request<ImpactExplanation>("/api/impact/explain", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, target, depth, refresh }),
  });
}

export function getOnboarding(sessionId: string): Promise<OnboardingGuide> {
  return request<OnboardingGuide>(`/api/onboarding/${encodeURIComponent(sessionId)}`);
}

export function getRepositorySummary(sessionId: string, refresh = false): Promise<RepositorySummary> {
  return request<RepositorySummary>("/api/onboarding/summary", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, refresh }),
  });
}
