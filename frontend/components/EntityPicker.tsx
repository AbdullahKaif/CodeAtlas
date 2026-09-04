"use client";

import { useEffect, useState } from "react";
import { ApiError, searchEntities } from "@/lib/api";
import type { EntitySummary } from "@/types/analysis";

/** Debounced search over the session's entities; empty query lists the most depended-upon ones. */
export default function EntityPicker({
  sessionId,
  onPick,
  placeholder = "Search a file, class, function or method…",
}: {
  sessionId: string;
  onPick: (entity: EntitySummary) => void;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<EntitySummary[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      searchEntities(sessionId, query, { limit: 12 })
        .then((response) => {
          if (!cancelled) {
            setResults(response.results);
            setError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof ApiError ? err.message : "Search failed.");
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId, query]);

  return (
    <div className="relative">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder}
        aria-label="Search entities"
        className="w-full rounded-lg border border-edge bg-surface px-3 py-2 font-mono text-sm text-ink outline-none placeholder:text-ink-3 focus:border-accent/60"
      />
      {error && <p className="mt-1 text-xs text-status-critical">{error}</p>}
      {open && results.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-80 w-full overflow-y-auto rounded-lg border border-edge bg-surface shadow-lg">
          {query.trim() === "" && (
            <li className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-ink-3">Most depended-upon</li>
          )}
          {results.map((entity) => (
            <li key={entity.id}>
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onPick(entity);
                  setQuery("");
                  setOpen(false);
                }}
                className="flex w-full items-center gap-3 px-3 py-2 text-left transition hover:bg-raised"
              >
                <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-3">{entity.type}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{entity.id}</span>
                {entity.dependents > 0 && (
                  <span className="shrink-0 text-[10px] text-ink-3">{entity.dependents} dep.</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
