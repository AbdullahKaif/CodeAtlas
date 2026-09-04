"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import AnswerText from "@/components/AnswerText";
import Card from "@/components/Card";
import EvidencePanel from "@/components/EvidencePanel";
import { ErrorPanel, LoadingPanel } from "@/components/LoadStates";
import { ApiError, getLLMHealth, getOnboarding, getRepositorySummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { OnboardingGuide, RepositorySummary } from "@/types/analysis";

export default function OnboardingPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [guide, setGuide] = useState<OnboardingGuide | null>(null);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const [llmReady, setLlmReady] = useState(false);
  const [summary, setSummary] = useState<
    { state: "idle" } | { state: "loading" } | { state: "error"; message: string } | { state: "done"; value: RepositorySummary }
  >({ state: "idle" });
  const [openStage, setOpenStage] = useState<string | null>("01");

  useEffect(() => {
    let cancelled = false;
    getOnboarding(sessionId)
      .then((g) => !cancelled && setGuide(g))
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof ApiError ? { message: err.message, status: err.status } : { message: "Failed to load onboarding.", status: -1 });
      });
    getLLMHealth().then((h) => !cancelled && setLlmReady(h.ready)).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  async function loadSummary(refresh = false) {
    setSummary({ state: "loading" });
    try {
      setSummary({ state: "done", value: await getRepositorySummary(sessionId, refresh) });
    } catch (err) {
      setSummary({ state: "error", message: err instanceof ApiError ? err.message : "Summary failed." });
    }
  }

  if (error) return <ErrorPanel message={error.message} gone={error.status === 404} />;
  if (!guide) return <LoadingPanel />;

  const chatLink = (q: string) => `/dashboard/${sessionId}/chat?q=${encodeURIComponent(q)}`;
  const { overview } = guide;

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-5">
        <h1 className="text-xl font-semibold">Onboarding · New Developer Mode</h1>
        <p className="mt-1 text-sm text-ink-2">
          A guided path through <span className="font-mono">{guide.repository}</span>, built from the
          repository&apos;s own structure. Every file and symbol below exists in the code.
        </p>
      </header>

      <Card
        title="01 · What this project is"
        action={
          summary.state === "done" ? (
            <button onClick={() => loadSummary(true)} className="text-xs text-ink-3 hover:text-ink">Regenerate AI summary</button>
          ) : (
            <button
              onClick={() => loadSummary()}
              disabled={summary.state === "loading" || !llmReady}
              title={llmReady ? undefined : "Set up Ollama to enable the AI summary"}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {summary.state === "loading" ? "Summarizing…" : "AI summary"}
            </button>
          )
        }
      >
        {overview.description ? (
          <p className="text-sm leading-relaxed text-ink-2">
            {overview.description}
            {overview.description_source && (
              <span className="ml-2 font-mono text-[11px] text-ink-3">from {overview.description_source}</span>
            )}
          </p>
        ) : (
          <p className="text-sm text-ink-3">No README description found.</p>
        )}
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-3">
          <span>{formatCount(overview.source_files)} source files</span>
          <span>{formatCount(overview.classes)} classes</span>
          <span>{formatCount(overview.functions)} functions</span>
          <span>{formatCount(overview.test_files)} test files</span>
          <span>{Object.keys(overview.languages).join(", ") || "no languages"}</span>
          {overview.security && (
            <Link href={`/dashboard/${sessionId}/security`} className="text-accent hover:underline">
              {overview.security.total} security findings ({overview.security.critical} critical, {overview.security.secrets} secrets)
            </Link>
          )}
        </div>
        {summary.state === "error" && (
          <p role="alert" className="mt-3 rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-xs text-ink-2">{summary.message}</p>
        )}
        {summary.state === "loading" && (
          <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
            <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
            Retrieving documentation and code, asking the local model…
          </p>
        )}
        {summary.state === "done" && (
          <div className="mt-4 rounded-lg border border-edge bg-raised p-4">
            <p className="mb-2 text-[10px] uppercase tracking-wider text-ink-3">AI summary · grounded in retrieved code{summary.value.cached && " · cached"}</p>
            <AnswerText text={summary.value.summary} />
            <EvidencePanel
              answer={{
                session_id: sessionId,
                question: "",
                answer: summary.value.summary,
                sources: summary.value.sources,
                context: summary.value.context,
                references_removed: summary.value.references_removed,
                model: summary.value.model,
                duration_seconds: 0,
              }}
            />
          </div>
        )}
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Architecture at a glance">
          <table className="w-full text-xs">
            <thead className="text-[10px] uppercase tracking-wider text-ink-3">
              <tr><th className="pb-1 text-left font-medium">Package</th><th className="pb-1 text-right font-medium">Files</th><th className="pb-1 text-right font-medium">Classes</th><th className="pb-1 text-right font-medium">Functions</th></tr>
            </thead>
            <tbody>
              {guide.architecture.packages.map((p) => (
                <tr key={p.name} className="border-t border-line">
                  <td className="py-1 font-mono text-ink-2">{p.name}</td>
                  <td className="py-1 text-right tabular-nums text-ink-3">{p.files}</td>
                  <td className="py-1 text-right tabular-nums text-ink-3">{p.classes}</td>
                  <td className="py-1 text-right tabular-nums text-ink-3">{p.functions}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {guide.architecture.hubs.length > 0 && (
            <>
              <p className="mt-3 text-[10px] uppercase tracking-wider text-ink-3">Most imported modules</p>
              <ul className="mt-1 space-y-0.5">
                {guide.architecture.hubs.map((h) => (
                  <li key={h.file} className="flex items-center justify-between font-mono text-xs">
                    <span className="truncate text-ink-2">{h.file}</span>
                    <span className="shrink-0 text-ink-3">imported by {h.imported_by}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <Link href={`/dashboard/${sessionId}/architecture`} className="mt-3 inline-block text-xs text-accent hover:underline">
            Open the interactive graph →
          </Link>
        </Card>

        <Card title="Important files">
          <ul className="space-y-2">
            {guide.important_files.slice(0, 8).map((f) => (
              <li key={f.path}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-mono text-xs text-ink">{f.path}</span>
                  <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(f.path)}`} className="shrink-0 text-[10px] text-accent hover:underline">impact</Link>
                </div>
                <p className="text-[11px] text-ink-3">{f.reasons.join(" · ")}{f.symbols.length > 0 && ` · ${f.symbols.join(", ")}`}</p>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <section className="mt-6">
        <h2 className="mb-3 text-sm font-medium">Guided sequence</h2>
        <ol className="space-y-2">
          {guide.stages.map((stage) => {
            const open = openStage === stage.number;
            return (
              <li key={stage.number} className={`rounded-xl border bg-surface ${stage.detected ? "border-edge" : "border-edge/50 opacity-70"}`}>
                <button onClick={() => setOpenStage(open ? null : stage.number)} className="flex w-full items-center gap-3 px-4 py-3 text-left">
                  <span className="font-mono text-xs text-accent">{stage.number}</span>
                  <span className="flex-1 text-sm font-medium">{stage.title}</span>
                  {!stage.detected && <span className="rounded-full border border-edge px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-3">not detected</span>}
                  <span className={`text-ink-3 transition ${open ? "rotate-90" : ""}`}>▸</span>
                </button>
                {open && (
                  <div className="border-t border-line px-4 py-3">
                    <p className="text-sm text-ink-2">{stage.explanation}</p>
                    {stage.files.length > 0 && (
                      <>
                        <p className="mt-3 text-[10px] uppercase tracking-wider text-ink-3">Files</p>
                        <ul className="mt-1 flex flex-wrap gap-1.5">
                          {stage.files.map((f) => (
                            <li key={f} className="rounded-md border border-edge bg-raised px-2 py-0.5 font-mono text-[11px] text-ink-2">{f}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {stage.symbols.length > 0 && (
                      <>
                        <p className="mt-3 text-[10px] uppercase tracking-wider text-ink-3">Symbols</p>
                        <ul className="mt-1 flex flex-wrap gap-1.5">
                          {stage.symbols.map((s) => (
                            <li key={s}>
                              <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(s)}`} className="rounded-md border border-edge bg-raised px-2 py-0.5 font-mono text-[11px] text-accent hover:border-accent/60">{s}</Link>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                    <p className="mt-3 text-[10px] uppercase tracking-wider text-ink-3">Questions to ask</p>
                    <ul className="mt-1 flex flex-wrap gap-1.5">
                      {stage.questions.map((q) => (
                        <li key={q}>
                          <Link href={chatLink(q)} className="rounded-full border border-edge px-3 py-1 text-xs text-ink-2 transition hover:border-accent/60 hover:text-ink">{q}</Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </section>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Reading order">
          <ol className="space-y-2">
            {guide.reading_order.map((step) => (
              <li key={step.order} className="flex gap-3">
                <span className="w-5 shrink-0 font-mono text-xs text-accent">{step.order}</span>
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs text-ink">{step.path}</p>
                  <p className="text-[11px] text-ink-3">{step.why}{step.symbols.length > 0 && ` (${step.symbols.join(", ")})`}</p>
                </div>
              </li>
            ))}
          </ol>
        </Card>
        <Card title="Learning path">
          {guide.learning_path.length === 0 ? (
            <p className="text-sm text-ink-3">Not enough evidence to plan a path.</p>
          ) : (
            <ol className="space-y-2">
              {guide.learning_path.map((d) => (
                <li key={d.day} className="flex gap-3">
                  <span className="w-12 shrink-0 font-mono text-xs text-accent">Day {d.day}</span>
                  <div className="min-w-0">
                    <p className="text-sm text-ink">{d.theme}</p>
                    <p className="text-[11px] text-ink-3">{d.goal}</p>
                    <p className="truncate font-mono text-[11px] text-ink-3">{d.files.join(" · ")}</p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>

      <div className="mt-4">
      <Card title="Key concepts">
        <ul className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {guide.key_concepts.map((c) => (
            <li key={`${c.kind}:${c.name}`} className="rounded-lg border border-edge bg-raised px-3 py-2">
              <div className="flex items-baseline gap-2">
                <span className="text-[10px] uppercase tracking-wider text-ink-3">{c.kind}</span>
                {c.entity_id ? (
                  <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(c.entity_id)}`} className="font-mono text-xs text-accent hover:underline">{c.name}</Link>
                ) : (
                  <span className="font-mono text-xs text-ink">{c.name}</span>
                )}
              </div>
              {c.summary && <p className="mt-0.5 text-[11px] text-ink-2">{c.summary}</p>}
              {c.file && <p className="font-mono text-[10px] text-ink-3">{c.file}</p>}
            </li>
          ))}
        </ul>
      </Card>
      </div>
      <p className="mt-4 text-[11px] text-ink-3">{guide.note}</p>
    </div>
  );
}
