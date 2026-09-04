"use client";

import { use, useEffect, useMemo, useState } from "react";
import FindingDetail from "@/components/FindingDetail";
import { ErrorPanel, LoadingPanel } from "@/components/LoadStates";
import SeverityBadge from "@/components/SeverityBadge";
import StatTile from "@/components/StatTile";
import { ApiError, getLLMHealth, getSecurityReport } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { Finding, ScannerStatus, SecurityReport, Severity } from "@/types/analysis";

const SEVERITIES: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

function ScannerLine({ status }: { status: ScannerStatus }) {
  const label = status.name === "semgrep" ? "Semgrep" : "Gitleaks";
  if (status.ran) {
    return (
      <li className="flex items-center gap-2 text-xs text-ink-2">
        <span className="h-1.5 w-1.5 rounded-full bg-status-good" />
        {label}
        {status.version && <span className="font-mono text-ink-3">v{status.version}</span>}
        <span className="text-ink-3">
          · {formatCount(status.findings)} finding{status.findings === 1 ? "" : "s"}
          {status.duration_seconds != null && ` · ${status.duration_seconds.toFixed(1)}s`}
        </span>
      </li>
    );
  }
  return (
    <li className="text-xs text-ink-2">
      <span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-status-warning align-middle" />
      {label}: {status.error ?? "did not run"}
      {status.install_hint && <span className="ml-1 text-ink-3">{status.install_hint}</span>}
    </li>
  );
}

export default function SecurityPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [report, setReport] = useState<SecurityReport | null>(null);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const [llmReady, setLlmReady] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<Severity | "ALL">("ALL");
  const [categoryFilter, setCategoryFilter] = useState<"all" | "vulnerability" | "secret">("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    getSecurityReport(sessionId)
      .then((result) => {
        if (!cancelled) setReport(result);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof ApiError
              ? { message: err.message, status: err.status }
              : { message: "Failed to load the security report.", status: -1 },
          );
      });
    getLLMHealth()
      .then((health) => {
        if (!cancelled) setLlmReady(health.ready);
      })
      .catch(() => {
        /* the buttons simply stay disabled */
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const visible = useMemo(() => {
    if (!report) return [];
    const needle = query.trim().toLowerCase();
    return report.findings.filter(
      (f) =>
        (severityFilter === "ALL" || f.severity === severityFilter) &&
        (categoryFilter === "all" || f.category === categoryFilter) &&
        (!needle ||
          f.file.toLowerCase().includes(needle) ||
          f.type.toLowerCase().includes(needle) ||
          f.rule.toLowerCase().includes(needle)),
    );
  }, [report, severityFilter, categoryFilter, query]);

  if (error) return <ErrorPanel message={error.message} gone={error.status === 404} />;
  if (!report) return <LoadingPanel />;

  const selected: Finding | undefined =
    report.findings.find((f) => f.id === selectedId) ?? visible[0];
  const counts = report.summary.by_severity;
  const nothingRan = report.scanners.every((s) => !s.ran);

  return (
    <div className="mx-auto max-w-6xl">
      <header className="mb-5">
        <h1 className="text-xl font-semibold">Security</h1>
        <p className="mt-1 text-sm text-ink-2">
          Deterministic findings from Semgrep and Gitleaks. Secret values are never shown. The
          local model can explain a finding and propose a fix; it never invents findings.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatTile label="Critical" value={formatCount(counts.CRITICAL ?? 0)} />
        <StatTile label="High" value={formatCount(counts.HIGH ?? 0)} />
        <StatTile label="Medium" value={formatCount(counts.MEDIUM ?? 0)} />
        <StatTile label="Low" value={formatCount((counts.LOW ?? 0) + (counts.INFO ?? 0))} />
        <StatTile label="Secrets" value={formatCount(report.summary.secrets)} />
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-1">
        {report.scanners.map((s) => (
          <ScannerLine key={s.name} status={s} />
        ))}
      </ul>
      {report.truncated && (
        <p className="mt-2 text-xs text-status-warning">
          Only the highest-severity findings are listed; the report was capped.
        </p>
      )}

      {nothingRan ? (
        <div className="mt-6 rounded-xl border border-status-warning/40 bg-status-warning/10 p-5 text-sm text-ink-2">
          No scanner could run, so there are no findings to show. Install Semgrep and/or Gitleaks
          (see the hints above), then re-analyze the repository.
        </div>
      ) : report.findings.length === 0 ? (
        <div className="mt-6 rounded-xl border border-edge bg-surface p-5 text-sm text-ink-2">
          The scanners that ran reported no findings. That is evidence, not a guarantee: only the
          bundled rules and the installed scanners were applied.
        </div>
      ) : (
        <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <section className="min-w-0 rounded-xl border border-edge bg-surface">
            <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
              <select
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value as Severity | "ALL")}
                aria-label="Filter by severity"
                className="rounded-md border border-edge bg-raised px-2 py-1 text-xs text-ink-2"
              >
                <option value="ALL">All severities</option>
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <select
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value as "all" | "vulnerability" | "secret")}
                aria-label="Filter by category"
                className="rounded-md border border-edge bg-raised px-2 py-1 text-xs text-ink-2"
              >
                <option value="all">Vulnerabilities + secrets</option>
                <option value="vulnerability">Vulnerabilities</option>
                <option value="secret">Secrets</option>
              </select>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter by file, type or rule"
                aria-label="Filter findings"
                className="min-w-0 flex-1 rounded-md border border-edge bg-raised px-2 py-1 font-mono text-xs text-ink outline-none placeholder:text-ink-3 focus:border-accent/60"
              />
              <span className="text-xs text-ink-3">{visible.length}/{report.findings.length}</span>
            </div>
            <ul className="max-h-[70vh] divide-y divide-line overflow-y-auto">
              {visible.map((f) => {
                const active = selected?.id === f.id;
                return (
                  <li key={f.id}>
                    <button
                      onClick={() => setSelectedId(f.id)}
                      className={`flex w-full items-start gap-3 px-3 py-2.5 text-left transition ${
                        active ? "bg-accent-soft" : "hover:bg-raised"
                      }`}
                    >
                      <SeverityBadge severity={f.severity} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-ink">{f.type}</p>
                        <p className="truncate font-mono text-[11px] text-ink-3">
                          {f.file}:{f.line} · {f.source}
                        </p>
                      </div>
                      <span className="shrink-0 font-mono text-[10px] text-ink-3">{f.id}</span>
                    </button>
                  </li>
                );
              })}
              {visible.length === 0 && (
                <li className="p-4 text-xs text-ink-3">No findings match these filters.</li>
              )}
            </ul>
          </section>

          <section className="min-w-0 rounded-xl border border-edge bg-surface p-5">
            {selected ? (
              <FindingDetail key={selected.id} sessionId={sessionId} finding={selected} llmReady={llmReady} />
            ) : (
              <p className="text-sm text-ink-3">Select a finding to inspect it.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
