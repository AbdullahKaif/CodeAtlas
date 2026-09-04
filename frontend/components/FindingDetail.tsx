"use client";

import { useState } from "react";
import AnswerText from "@/components/AnswerText";
import DiffView from "@/components/DiffView";
import EvidencePanel from "@/components/EvidencePanel";
import SeverityBadge from "@/components/SeverityBadge";
import { ApiError, explainFinding, suggestFix } from "@/lib/api";
import type { Finding, SecurityExplanation, SecurityFix } from "@/types/analysis";

type Loadable<T> = { state: "idle" } | { state: "loading" } | { state: "error"; message: string } | { state: "done"; value: T };

export default function FindingDetail({
  sessionId,
  finding,
  llmReady,
}: {
  sessionId: string;
  finding: Finding;
  llmReady: boolean;
}) {
  // The parent keys this component by finding id, so a different finding
  // remounts it with fresh AI panels.
  const [explanation, setExplanation] = useState<Loadable<SecurityExplanation>>({ state: "idle" });
  const [fix, setFix] = useState<Loadable<SecurityFix>>({ state: "idle" });

  async function loadExplanation(refresh = false) {
    setExplanation({ state: "loading" });
    try {
      setExplanation({ state: "done", value: await explainFinding(sessionId, finding.id, refresh) });
    } catch (err) {
      setExplanation({ state: "error", message: err instanceof ApiError ? err.message : "Explanation failed." });
    }
  }

  async function loadFix(refresh = false) {
    setFix({ state: "loading" });
    try {
      setFix({ state: "done", value: await suggestFix(sessionId, finding.id, refresh) });
    } catch (err) {
      setFix({ state: "error", message: err instanceof ApiError ? err.message : "Fix suggestion failed." });
    }
  }

  const location = finding.end_line && finding.end_line !== finding.line
    ? `${finding.file}:${finding.line}-${finding.end_line}`
    : `${finding.file}:${finding.line}`;

  return (
    <div className="space-y-5">
      <header>
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <span className="font-mono text-xs text-ink-3">{finding.id}</span>
          <span className="rounded-full border border-edge px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-3">
            {finding.category}
          </span>
        </div>
        <h2 className="mt-2 text-lg font-semibold">{finding.type}</h2>
        <p className="mt-1 font-mono text-xs text-ink-2">{location}</p>
      </header>

      <section>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-3">Scanner message</h3>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">{finding.message}</p>
        <dl className="mt-3 grid grid-cols-[6rem_1fr] gap-y-1 text-xs">
          <dt className="text-ink-3">Scanner</dt>
          <dd className="font-mono text-ink-2">{finding.source}</dd>
          <dt className="text-ink-3">Rule</dt>
          <dd className="break-all font-mono text-ink-2">{finding.rule}</dd>
          {finding.cwe.length > 0 && (
            <>
              <dt className="text-ink-3">CWE</dt>
              <dd className="text-ink-2">{finding.cwe.join("; ")}</dd>
            </>
          )}
          {finding.owasp.length > 0 && (
            <>
              <dt className="text-ink-3">OWASP</dt>
              <dd className="text-ink-2">{finding.owasp.join("; ")}</dd>
            </>
          )}
        </dl>
        {finding.references.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs">
            {finding.references.slice(0, 3).map((ref) => (
              <li key={ref}>
                <a href={ref} target="_blank" rel="noreferrer" className="break-all text-accent hover:underline">
                  {ref}
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-3">
          Flagged code{finding.category === "secret" && " · secret value redacted"}
        </h3>
        {finding.code_context ? (
          <pre className="mt-1 overflow-x-auto rounded-lg border border-edge bg-page p-3 font-mono text-[11px] leading-relaxed text-ink-2">
            {finding.code_context.split("\n").map((line, i) => (
              <div key={i} className="grid grid-cols-[3.5rem_1fr]">
                <span className="select-none pr-3 text-right text-ink-3">{finding.line + i}</span>
                <span>{line || " "}</span>
              </div>
            ))}
          </pre>
        ) : (
          <p className="mt-1 text-xs text-ink-3">The flagged lines could not be read from the clone.</p>
        )}
      </section>

      <section className="rounded-xl border border-edge bg-surface p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium">AI explanation</h3>
          {explanation.state === "done" ? (
            <button onClick={() => loadExplanation(true)} className="text-xs text-ink-3 transition hover:text-ink">
              Regenerate
            </button>
          ) : (
            <button
              onClick={() => loadExplanation()}
              disabled={explanation.state === "loading" || !llmReady}
              title={llmReady ? undefined : "Set up Ollama to enable AI explanations"}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {explanation.state === "loading" ? "Explaining…" : "Explain with AI"}
            </button>
          )}
        </div>
        <p className="mt-1 text-xs text-ink-3">
          The scanner result above is the fact; the local model only explains it, using the flagged
          code and related retrieved context.
        </p>
        {explanation.state === "loading" && (
          <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
            <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
            Retrieving code and asking the local model…
          </p>
        )}
        {explanation.state === "error" && (
          <p role="alert" className="mt-3 rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-xs text-ink-2">
            {explanation.message}
          </p>
        )}
        {explanation.state === "done" && (
          <div className="mt-3">
            <ExplanationText text={explanation.value.explanation} />
            <EvidencePanel
              answer={{
                session_id: sessionId,
                question: "",
                answer: explanation.value.explanation,
                sources: explanation.value.sources,
                context: explanation.value.context,
                references_removed: explanation.value.references_removed,
                model: explanation.value.model,
                duration_seconds: explanation.value.duration_seconds,
              }}
            />
            {explanation.value.cached && <p className="mt-1 text-[11px] text-ink-3">Loaded from this session&apos;s cache.</p>}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-edge bg-surface p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium">Suggested fix</h3>
          {fix.state === "done" ? (
            <button onClick={() => loadFix(true)} className="text-xs text-ink-3 transition hover:text-ink">
              Regenerate
            </button>
          ) : (
            <button
              onClick={() => loadFix()}
              disabled={fix.state === "loading" || !llmReady}
              title={llmReady ? undefined : "Set up Ollama to enable fix suggestions"}
              className="rounded-lg border border-edge px-3 py-1.5 text-xs font-medium text-ink transition hover:bg-raised disabled:cursor-not-allowed disabled:opacity-40"
            >
              {fix.state === "loading" ? "Thinking…" : "Suggest a fix"}
            </button>
          )}
        </div>
        {fix.state === "loading" && (
          <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
            <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
            Asking the local model for a minimal change…
          </p>
        )}
        {fix.state === "error" && (
          <p role="alert" className="mt-3 rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-xs text-ink-2">
            {fix.message}
          </p>
        )}
        {fix.state === "done" && (
          <div className="mt-3 space-y-3">
            <p className="rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-1.5 text-xs text-ink-2">
              {fix.value.disclaimer} Nothing is written to the repository.
            </p>
            {fix.value.explanation && <AnswerText text={fix.value.explanation} />}
            {fix.value.diff ? (
              <>
                <p className="text-[11px] uppercase tracking-wider text-ink-3">
                  Unified diff · lines {fix.value.region_start_line}–{fix.value.region_end_line}
                </p>
                <DiffView diff={fix.value.diff} />
              </>
            ) : (
              <p className="text-xs text-ink-3">The model did not return a code change for this finding.</p>
            )}
            {fix.value.side_effects && (
              <div>
                <p className="text-[11px] uppercase tracking-wider text-ink-3">Potential side effects</p>
                <AnswerText text={fix.value.side_effects} />
              </div>
            )}
            <p className="text-[11px] text-ink-3">
              {fix.value.model} · {fix.value.duration_seconds.toFixed(1)}s · local inference
              {fix.value.cached && " · cached"}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

/** Explanation markdown: promote "## Heading" lines to headings, render the rest as answer text. */
function ExplanationText({ text }: { text: string }) {
  const sections = text.split(/^##\s+/m).filter((s) => s.trim());
  if (sections.length <= 1) return <AnswerText text={text} />;
  return (
    <div className="space-y-4">
      {sections.map((section, i) => {
        const [heading, ...rest] = section.split("\n");
        return (
          <div key={i}>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-ink-3">{heading.trim()}</h4>
            <AnswerText text={rest.join("\n").trim()} />
          </div>
        );
      })}
    </div>
  );
}
