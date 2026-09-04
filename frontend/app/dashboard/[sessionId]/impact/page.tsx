"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AnswerText from "@/components/AnswerText";
import EntityPicker from "@/components/EntityPicker";
import EvidencePanel from "@/components/EvidencePanel";
import LevelBadge from "@/components/LevelBadge";
import { ApiError, analyzeImpact, explainImpact, getLLMHealth } from "@/lib/api";
import type { AffectedEntity, ImpactExplanation, ImpactResult } from "@/types/analysis";

const VIA_LABEL: Record<AffectedEntity["via"], string> = {
  calls: "calls",
  imports: "imports",
  inherits: "inherits from",
  member: "member of",
};

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

export default function ImpactPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [target, setTarget] = useState<string | null>(null);
  const [depth, setDepth] = useState(2);
  const [result, setResult] = useState<ImpactResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [llmReady, setLlmReady] = useState(false);
  const [explanation, setExplanation] = useState<
    { state: "idle" } | { state: "loading" } | { state: "error"; message: string } | { state: "done"; value: ImpactExplanation }
  >({ state: "idle" });

  const run = useCallback(
    async (id: string, d: number) => {
      setBusy(true);
      setError(null);
      setExplanation({ state: "idle" });
      try {
        setResult(await analyzeImpact(sessionId, id, d));
      } catch (err) {
        setResult(null);
        setError(err instanceof ApiError ? err.message : "Impact analysis failed.");
      } finally {
        setBusy(false);
      }
    },
    [sessionId],
  );

  useEffect(() => {
    let cancelled = false;
    Promise.resolve().then(() => {
      if (cancelled) return;
      getLLMHealth().then((h) => !cancelled && setLlmReady(h.ready)).catch(() => undefined);
      try {
        const initial = new URLSearchParams(window.location.search).get("target");
        if (initial) {
          setTarget(initial);
          run(initial, 2);
        }
      } catch {
        /* ignore */
      }
    });
    return () => {
      cancelled = true;
    };
  }, [run]);

  function select(id: string) {
    setTarget(id);
    run(id, depth);
  }

  function changeDepth(d: number) {
    setDepth(d);
    if (target) run(target, d);
  }

  async function explain(refresh = false) {
    if (!target) return;
    setExplanation({ state: "loading" });
    try {
      setExplanation({ state: "done", value: await explainImpact(sessionId, target, depth, refresh) });
    } catch (err) {
      setExplanation({ state: "error", message: err instanceof ApiError ? err.message : "Explanation failed." });
    }
  }

  const byDepth = new Map<number, AffectedEntity[]>();
  for (const a of result?.affected ?? []) byDepth.set(a.depth, [...(byDepth.get(a.depth) ?? []), a]);

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-5">
        <h1 className="text-xl font-semibold">Impact Analysis</h1>
        <p className="mt-1 text-sm text-ink-2">
          Pick a file, class, function or method to see what statically depends on it: callers,
          importers, subclasses and transitive dependents. Static / AI-assisted, never a guarantee.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <EntityPicker sessionId={sessionId} onPick={(e) => select(e.id)} />
        </div>
        <label className="flex items-center gap-2 text-xs text-ink-3">
          Depth
          <select
            value={depth}
            onChange={(e) => changeDepth(Number(e.target.value))}
            className="rounded-md border border-edge bg-raised px-2 py-1 text-xs text-ink-2"
          >
            <option value={1}>1 · direct only</option>
            <option value={2}>2</option>
            <option value={3}>3</option>
          </select>
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-4 rounded-lg border border-status-critical/40 bg-status-critical/10 px-4 py-3 text-sm text-ink-2">
          {error}
        </p>
      )}
      {busy && <p className="mt-4 text-sm text-ink-3">Analyzing dependents…</p>}

      {!result && !busy && !error && (
        <div className="mt-6 rounded-xl border border-edge bg-surface p-6 text-sm text-ink-3">
          Select a component above. The empty search lists the most depended-upon entities in this repository.
        </div>
      )}

      {result && (
        <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section className="min-w-0 rounded-xl border border-edge bg-surface p-5">
            <div className="flex flex-wrap items-center gap-2">
              <LevelBadge level={result.level} />
              <span className="text-[10px] uppercase tracking-wider text-ink-3">{result.target.type}</span>
            </div>
            <h2 className="mt-2 break-all font-mono text-base font-semibold">{result.target.id}</h2>
            <p className="mt-1 font-mono text-xs text-ink-3">
              {result.target.file}:{result.target.start_line}–{result.target.end_line}
              {result.target.members > 0 && ` · ${result.target.members} member${result.target.members === 1 ? "" : "s"} included`}
            </p>
            {result.target.signature && (
              <pre className="mt-2 overflow-x-auto rounded-md border border-edge bg-page px-3 py-1.5 font-mono text-[11px] text-ink-2">
                {result.target.signature}
              </pre>
            )}
            <div className="mt-4 grid grid-cols-3 gap-2">
              {[
                ["Callers", result.counts.callers],
                ["Importers", result.counts.importers],
                ["Subclasses", result.counts.subclasses],
                ["Transitive", result.counts.transitive],
                ["Files", result.counts.files],
                ["Tests", result.counts.tests],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-edge bg-raised p-2 text-center">
                  <p className="text-lg font-semibold tabular-nums">{value}</p>
                  <p className="text-[10px] uppercase tracking-wider text-ink-3">{label}</p>
                </div>
              ))}
            </div>
            <h3 className="mt-4 text-[11px] font-medium uppercase tracking-wider text-ink-3">Reason</h3>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink-2">
              {result.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            {result.tests.length > 0 && (
              <>
                <h3 className="mt-4 text-[11px] font-medium uppercase tracking-wider text-ink-3">Tests to run</h3>
                <ul className="mt-1 space-y-0.5">
                  {result.tests.map((t) => (
                    <li key={t} className="font-mono text-xs text-ink-2">{t}</li>
                  ))}
                </ul>
              </>
            )}
            <p className="mt-4 text-[11px] leading-relaxed text-ink-3">{result.note}</p>
            <div className="mt-3 flex gap-3 text-xs">
              <Link href={`/dashboard/${sessionId}/architecture?focus=${encodeURIComponent(result.target.id)}`} className="text-accent hover:underline">
                View in architecture graph →
              </Link>
            </div>
          </section>

          <section className="min-w-0 space-y-4">
            <div className="rounded-xl border border-edge bg-surface p-5">
              <h3 className="text-sm font-medium">
                Potentially affected
                <span className="ml-2 text-xs font-normal text-ink-3">{result.affected.length}{result.truncated && "+"}</span>
              </h3>
              {result.affected.length === 0 ? (
                <p className="mt-2 text-sm text-ink-3">No static dependents found. Dynamic uses may still exist.</p>
              ) : (
                [...byDepth.entries()].sort(([a], [b]) => a - b).map(([d, items]) => (
                  <div key={d} className="mt-3">
                    <p className="text-[10px] uppercase tracking-wider text-ink-3">
                      {d === 1 ? "Direct" : `Depth ${d}`} · {items.length}
                    </p>
                    <ul className="mt-1 divide-y divide-line">
                      {items.map((a) => (
                        <li key={a.id} className="py-1.5">
                          <div className="flex items-baseline gap-2">
                            <span className="w-14 shrink-0 text-[10px] uppercase tracking-wider text-ink-3">{a.type}</span>
                            <button onClick={() => select(a.id)} title="Analyze this component" className="min-w-0 flex-1 truncate text-left font-mono text-xs text-ink hover:text-accent">
                              {a.id}
                            </button>
                            {a.is_test && <span className="shrink-0 rounded-full border border-edge px-1.5 text-[9px] uppercase tracking-wider text-ink-3">test</span>}
                          </div>
                          <p className="ml-16 font-mono text-[10px] text-ink-3">
                            {VIA_LABEL[a.via]} {a.through}{a.line != null && ` · line ${a.line}`}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))
              )}
            </div>

            <div className="rounded-xl border border-edge bg-surface p-5">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-medium">AI reading of the impact</h3>
                {explanation.state === "done" ? (
                  <button onClick={() => explain(true)} className="text-xs text-ink-3 transition hover:text-ink">Regenerate</button>
                ) : (
                  <button
                    onClick={() => explain()}
                    disabled={explanation.state === "loading" || !llmReady}
                    title={llmReady ? undefined : "Set up Ollama to enable AI explanations"}
                    className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {explanation.state === "loading" ? "Thinking…" : "Explain consequences"}
                  </button>
                )}
              </div>
              <p className="mt-1 text-xs text-ink-3">Uses the target&apos;s code and its nearest dependents as evidence; citations are validated.</p>
              {explanation.state === "loading" && (
                <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
                  <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
                  Asking the local model…
                </p>
              )}
              {explanation.state === "error" && (
                <p role="alert" className="mt-3 rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-xs text-ink-2">{explanation.message}</p>
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
                  <p className="mt-2 text-[11px] text-ink-3">{explanation.value.note}{explanation.value.cached && " · cached"}</p>
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
