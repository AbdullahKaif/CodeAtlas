"use client";

import { useEffect, useState } from "react";
import { ApiError, getOverview } from "@/lib/api";
import { cachedOverview } from "@/lib/sessions";
import type { AnalyzeResponse } from "@/types/analysis";

export function useOverview(sessionId: string) {
  const [data, setData] = useState<AnalyzeResponse | null>(null);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const cached = cachedOverview(sessionId);
    // Cached results resolve through the same promise path so state updates
    // stay asynchronous (post-hydration) in both branches.
    const load = cached ? Promise.resolve(cached) : getOverview(sessionId);
    load
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof ApiError
              ? { message: err.message, status: err.status }
              : { message: "Failed to load the analysis.", status: -1 },
          );
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return { data, error };
}
