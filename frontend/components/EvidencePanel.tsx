"use client";

import { useState } from "react";
import type { ChatAnswer, RetrievedChunk, SourceReference } from "@/types/analysis";

function lineLabel(start?: number | null, end?: number | null): string {
  if (start == null) return "";
  return end != null && end !== start ? `L${start}–${end}` : `L${start}`;
}

function CodeExcerpt({ chunk }: { chunk: RetrievedChunk }) {
  return (
    <pre className="mt-2 overflow-x-auto rounded-lg border border-edge bg-page p-3 font-mono text-[11px] leading-relaxed text-ink-2">
      {chunk.text.split("\n").map((line, i) => (
        <div key={i} className="grid grid-cols-[3.5rem_1fr]">
          <span className="select-none pr-3 text-right text-ink-3">{chunk.start_line + i}</span>
          <span>{line || " "}</span>
        </div>
      ))}
    </pre>
  );
}

function SourceChip({
  source,
  chunk,
}: {
  source: SourceReference;
  chunk: RetrievedChunk | undefined;
}) {
  const [open, setOpen] = useState(false);
  const lines = lineLabel(source.start_line, source.end_line);
  return (
    <li>
      <button
        onClick={() => chunk && setOpen((v) => !v)}
        title={chunk ? (open ? "Hide the cited code" : "Show the cited code") : undefined}
        className={`flex items-center gap-2 rounded-md border border-edge bg-raised px-2 py-1 text-left font-mono text-xs transition ${
          chunk ? "hover:border-accent/60" : "cursor-default"
        } ${open ? "border-accent/60" : ""}`}
      >
        <span className="text-ink">{source.file}</span>
        {lines && <span className="text-ink-3">{lines}</span>}
        {source.symbol && <span className="text-accent">{source.symbol}</span>}
      </button>
      {open && chunk && <CodeExcerpt chunk={chunk} />}
    </li>
  );
}

function ContextRow({ chunk }: { chunk: RetrievedChunk }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-1.5">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 text-left font-mono text-xs"
      >
        <span className="w-10 shrink-0 tabular-nums text-ink-3">{chunk.score.toFixed(2)}</span>
        <span className="truncate text-ink-2">{chunk.file}</span>
        <span className="shrink-0 text-ink-3">{lineLabel(chunk.start_line, chunk.end_line)}</span>
        {chunk.symbol && <span className="truncate text-accent">{chunk.symbol}</span>}
        <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wider text-ink-3">
          {chunk.type}
        </span>
      </button>
      {open && <CodeExcerpt chunk={chunk} />}
    </li>
  );
}

export default function EvidencePanel({ answer }: { answer: ChatAnswer }) {
  const byId = new Map(answer.context.map((c) => [c.chunk_id, c]));
  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[11px] font-medium uppercase tracking-wider text-ink-3">
          Verified sources
        </span>
        {answer.references_removed > 0 && (
          <span className="text-[11px] text-status-warning">
            {answer.references_removed} unverifiable reference
            {answer.references_removed === 1 ? "" : "s"} removed
          </span>
        )}
      </div>
      {answer.sources.length === 0 ? (
        <p className="mt-1.5 text-xs text-ink-3">
          The model cited no verifiable sources for this answer - treat it with care and check
          the evidence below.
        </p>
      ) : (
        <ul className="mt-2 flex flex-wrap gap-2">
          {answer.sources.map((s, i) => (
            <SourceChip key={i} source={s} chunk={s.chunk_id ? byId.get(s.chunk_id) : undefined} />
          ))}
        </ul>
      )}

      <details className="group mt-3">
        <summary className="cursor-pointer list-none text-[11px] font-medium uppercase tracking-wider text-ink-3 transition hover:text-ink-2">
          <span className="mr-1 inline-block transition group-open:rotate-90">▸</span>
          Evidence shown to the model · {answer.context.length} chunk
          {answer.context.length === 1 ? "" : "s"}
        </summary>
        <ul className="mt-1 divide-y divide-line">
          {answer.context.map((c) => (
            <ContextRow key={c.chunk_id} chunk={c} />
          ))}
        </ul>
      </details>
      <p className="mt-2 text-[11px] text-ink-3">
        {answer.model} · {answer.duration_seconds.toFixed(1)}s · local inference
      </p>
    </div>
  );
}
