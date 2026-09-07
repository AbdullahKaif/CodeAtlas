"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import AnswerText from "@/components/AnswerText";
import Card from "@/components/Card";
import EvidencePanel from "@/components/EvidencePanel";
import LanguageBars from "@/components/LanguageBars";
import { ErrorPanel, LoadingPanel } from "@/components/LoadStates";
import StatTile from "@/components/StatTile";
import { useOverview } from "@/components/useOverview";
import { ApiError, getHealthIndicators, getLLMHealth, getOnboarding, getRepositorySummary } from "@/lib/api";
import { formatBytes, formatCount } from "@/lib/format";
import type { HealthIndicators, OnboardingGuide, RepositorySummary } from "@/types/analysis";

function percent(value: number | null): string {
  return value === null ? "n/a" : `${Math.round(value * 100)}%`;
}

function Indicator({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-edge bg-raised px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-ink-3">{label}</p>
      <p className="mt-0.5 text-base font-semibold tabular-nums text-ink">{value}</p>
      {hint && <p className="truncate text-[11px] text-ink-3" title={hint}>{hint}</p>}
    </div>
  );
}

function HealthCard({ health, sessionId }: { health: HealthIndicators; sessionId: string }) {
  const { security, documentation: doc, maintainability: m, dependencies: deps } = health;
  const t = m.thresholds;
  return (
    <Card title="Codebase Health Indicators">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Indicator
          label="Security"
          value={security.scanned ? `${security.critical + security.high} high+` : "not scanned"}
          hint={security.scanned ? `${security.medium} medium · ${security.low} low · ${security.secrets} secrets` : security.note}
        />
        <Indicator
          label="Docstring coverage"
          value={percent(doc.docstring_coverage)}
          hint={`${formatCount(doc.documented_entities)} of ${formatCount(doc.documentable_entities)} entities`}
        />
        <Indicator
          label="Oversized"
          value={`${m.very_large_files_total} files · ${m.very_large_functions_total} fns`}
          hint={`over ${t.very_large_file_lines} / ${t.very_large_function_lines} lines`}
        />
        <Indicator
          label="Dependencies"
          value={deps.count === null ? "n/a" : formatCount(deps.count)}
          hint={deps.files.length ? deps.files.join(", ") : "no dependency file found"}
        />
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
        <div className="flex justify-between gap-3">
          <dt className="text-ink-3">README</dt>
          <dd className="font-mono text-ink-2">{doc.readme_present ? doc.readme_path : "missing"}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-3">Documentation files</dt>
          <dd className="text-ink-2">{formatCount(doc.documentation_files)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-3">Median / longest function</dt>
          <dd className="tabular-nums text-ink-2">{m.median_function_lines} / {m.longest_function_lines} lines</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-3">Largest file</dt>
          <dd className="tabular-nums text-ink-2">{formatCount(m.largest_file_lines)} lines</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-3">Functions with more than {t.many_parameters} parameters</dt>
          <dd className="text-ink-2">{formatCount(m.many_parameter_functions_total)}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-ink-3">Pinned dependencies</dt>
          <dd className="text-ink-2">{deps.pinned === null ? "n/a" : `${deps.pinned} of ${deps.count}`}</dd>
        </div>
      </dl>

      {(m.very_large_functions.length > 0 || m.very_large_files.length > 0 || m.many_parameter_functions.length > 0) && (
        <details className="group mt-3">
          <summary className="cursor-pointer list-none text-[11px] font-medium uppercase tracking-wider text-ink-3 transition hover:text-ink-2">
            <span className="mr-1 inline-block transition group-open:rotate-90">▸</span>
            Oversized items
          </summary>
          <ul className="mt-1 space-y-0.5">
            {m.very_large_files.map((i) => (
              <li key={`f-${i.id}`} className="flex justify-between font-mono text-[11px]">
                <span className="truncate text-ink-2">{i.id}</span>
                <span className="shrink-0 text-ink-3">{formatCount(i.lines)} lines</span>
              </li>
            ))}
            {m.very_large_functions.map((i) => (
              <li key={`fn-${i.id}`} className="flex justify-between font-mono text-[11px]">
                <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(i.id)}`} className="truncate text-accent hover:underline">{i.id}</Link>
                <span className="shrink-0 text-ink-3">{i.lines} lines</span>
              </li>
            ))}
            {m.many_parameter_functions.map((i) => (
              <li key={`p-${i.id}`} className="flex justify-between font-mono text-[11px]">
                <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(i.id)}`} className="truncate text-accent hover:underline">{i.id}</Link>
                <span className="shrink-0 text-ink-3">{i.parameters} parameters</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="mt-3 text-[11px] text-ink-3">{health.note}</p>
    </Card>
  );
}

export default function OverviewPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const { data, error } = useOverview(sessionId);
  const [health, setHealth] = useState<HealthIndicators | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [guide, setGuide] = useState<OnboardingGuide | null>(null);
  const [llmReady, setLlmReady] = useState(false);
  const [summary, setSummary] = useState<
    { state: "idle" } | { state: "loading" } | { state: "error"; message: string } | { state: "done"; value: RepositorySummary }
  >({ state: "idle" });

  useEffect(() => {
    let cancelled = false;
    getHealthIndicators(sessionId)
      .then((h) => !cancelled && setHealth(h))
      .catch((err) => !cancelled && setHealthError(err instanceof ApiError ? err.message : "Health indicators unavailable."));
    getOnboarding(sessionId)
      .then((g) => !cancelled && setGuide(g))
      .catch(() => undefined);
    getLLMHealth()
      .then((h) => !cancelled && setLlmReady(h.ready))
      .catch(() => undefined);
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
  if (!data) return <LoadingPanel />;

  const { repository, scan, security } = data;
  const totalLines = scan.files.reduce((sum, f) => sum + (f.line_count ?? 0), 0);
  const testCount = scan.files.filter((f) => f.is_test_file).length;
  const largest = [...scan.files].sort((a, b) => b.size_bytes - a.size_bytes).slice(0, 8);

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6">
        <h1 className="font-mono text-xl font-semibold">{repository.name}</h1>
        <a
          href={repository.url.replace(/\.git$/, "")}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-ink-3 transition hover:text-accent"
        >
          {repository.url}
        </a>
      </header>

      {scan.summary.truncated && (
        <div className="mb-4 rounded-lg border border-status-warning/40 bg-status-warning/10 px-4 py-3 text-sm text-ink-2">
          ⚠ Very large repository - the inventory below covers the first{" "}
          {formatCount(scan.summary.files_included)} files.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <StatTile label="Files" value={formatCount(scan.summary.files_included)} />
        <StatTile label="Lines of code" value={formatCount(totalLines)} />
        <StatTile label="Languages" value={formatCount(Object.keys(scan.languages).length)} />
        <StatTile label="Size" value={formatBytes(scan.total_size_bytes)} />
        <StatTile
          label="Classes"
          value={formatCount(data.parse?.entities?.class ?? 0)}
          hint={data.parse ? `${formatCount((data.parse.entities.function ?? 0) + (data.parse.entities.method ?? 0))} functions` : undefined}
        />
        <StatTile label="Test files" value={formatCount(testCount)} />
      </div>

      <div className="mt-4">
        <Card
          title="What this repository is"
          action={
            summary.state === "done" ? (
              <button onClick={() => loadSummary(true)} className="text-xs text-ink-3 hover:text-ink">Regenerate</button>
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
          {guide?.overview.description ? (
            <p className="text-sm leading-relaxed text-ink-2">
              {guide.overview.description}
              {guide.overview.description_source && (
                <span className="ml-2 font-mono text-[11px] text-ink-3">from {guide.overview.description_source}</span>
              )}
            </p>
          ) : (
            <p className="text-sm text-ink-3">{guide ? "No README description found." : "Loading…"}</p>
          )}
          {health && health.frameworks.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {health.frameworks.map((f) => (
                <span key={f.name} title={`${f.category} · ${f.evidence}`} className="rounded-md border border-edge bg-raised px-2 py-0.5 text-xs text-ink-2">
                  {f.name}
                </span>
              ))}
            </div>
          )}
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
      </div>

      <div className="mt-4">
        {health ? (
          <HealthCard health={health} sessionId={sessionId} />
        ) : (
          <Card title="Codebase Health Indicators">
            <p className="text-sm text-ink-3">{healthError ?? "Computing indicators…"}</p>
          </Card>
        )}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card
          title="Security summary"
          action={
            <Link href={`/dashboard/${sessionId}/security`} className="text-xs text-accent hover:underline">
              Open security →
            </Link>
          }
        >
          {security ? (
            <>
              <div className="grid grid-cols-5 gap-2">
                {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((level) => (
                  <div key={level} className="rounded-lg border border-edge bg-raised p-2 text-center">
                    <p className="text-lg font-semibold tabular-nums">
                      {formatCount(security.summary.by_severity[level] ?? 0)}
                    </p>
                    <p className="text-[10px] uppercase tracking-wider text-ink-3">{level}</p>
                  </div>
                ))}
                <div className="rounded-lg border border-edge bg-raised p-2 text-center">
                  <p className="text-lg font-semibold tabular-nums">{formatCount(security.summary.secrets)}</p>
                  <p className="text-[10px] uppercase tracking-wider text-ink-3">Secrets</p>
                </div>
              </div>
              <ul className="mt-3 space-y-0.5 text-xs text-ink-3">
                {security.scanners.map((s) => (
                  <li key={s.name}>
                    {s.name === "semgrep" ? "Semgrep" : "Gitleaks"}:{" "}
                    {s.ran ? `ran (${formatCount(s.findings)} findings)` : s.error ?? "did not run"}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-ink-3">
              {data.security_error ?? "No security scan for this session. Re-analyze to run the scanners."}
            </p>
          )}
        </Card>

        <Card
          title="Important modules"
          action={
            <Link href={`/dashboard/${sessionId}/onboarding`} className="text-xs text-accent hover:underline">
              Open onboarding →
            </Link>
          }
        >
          {guide ? (
            guide.important_files.length === 0 ? (
              <p className="text-sm text-ink-3">No modules stood out structurally.</p>
            ) : (
              <ul className="space-y-1.5">
                {guide.important_files.slice(0, 6).map((f) => (
                  <li key={f.path}>
                    <div className="flex items-baseline justify-between gap-2">
                      <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(f.path)}`} className="truncate font-mono text-xs text-accent hover:underline">{f.path}</Link>
                    </div>
                    <p className="truncate text-[11px] text-ink-3">{f.reasons.join(" · ")}</p>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <p className="text-sm text-ink-3">Loading…</p>
          )}
        </Card>

        <Card title="Languages">
          <LanguageBars languages={scan.languages} />
        </Card>

        <Card title="Dependencies">
          {health ? (
            health.dependencies.count === null ? (
              <p className="text-sm text-ink-3">No dependency file could be read.</p>
            ) : (
              <>
                <p className="text-xs text-ink-3">
                  {formatCount(health.dependencies.count)} declared in{" "}
                  <span className="font-mono text-ink-2">{health.dependencies.files.join(", ")}</span>
                  {health.dependencies.unparsed_files.length > 0 && (
                    <> · could not parse {health.dependencies.unparsed_files.join(", ")}</>
                  )}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {health.dependencies.packages.map((p) => (
                    <span key={p} className="rounded-md border border-edge bg-raised px-2 py-0.5 font-mono text-[11px] text-ink-2">{p}</span>
                  ))}
                  {health.dependencies.count > health.dependencies.packages.length && (
                    <span className="px-1 text-[11px] text-ink-3">+{health.dependencies.count - health.dependencies.packages.length} more</span>
                  )}
                </div>
              </>
            )
          ) : (
            <p className="text-sm text-ink-3">Loading…</p>
          )}
        </Card>

        <Card title="Entry points">
          {scan.entry_points.length === 0 ? (
            <p className="text-sm text-ink-3">No conventional entry points detected.</p>
          ) : (
            <ul className="space-y-1.5">
              {scan.entry_points.map((p) => (
                <li key={p} className="truncate font-mono text-sm text-ink-2">
                  {p}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Project files">
          {scan.project_files.length === 0 ? (
            <p className="text-sm text-ink-3">No standard project files found.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {scan.project_files.map((p) => (
                <span
                  key={p}
                  className="rounded-md border border-edge bg-raised px-2 py-1 font-mono text-xs text-ink-2"
                >
                  {p}
                </span>
              ))}
            </div>
          )}
        </Card>

        <Card title="Largest files">
          <table className="w-full text-sm">
            <tbody>
              {largest.map((f) => (
                <tr key={f.path} className="border-b border-line last:border-0">
                  <td title={f.path} className="max-w-0 py-1.5 pr-3">
                    <div className="flex min-w-0 items-baseline gap-2">
                      <span className="shrink-0 font-mono text-xs text-ink-2">{f.name}</span>
                      <span className="truncate font-mono text-[10px] text-ink-3">
                        {f.path.split("/").slice(0, -1).join("/")}
                      </span>
                    </div>
                  </td>
                  <td className="whitespace-nowrap py-1.5 text-right text-xs tabular-nums text-ink-3">
                    {formatBytes(f.size_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
