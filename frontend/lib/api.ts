import type {
  AnalysisStatus,
  AnalyzeResponse,
  AnalyzeStarted,
  ChatAnswer,
  ChatMessage,
  LLMHealth,
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
