"use client";

import { use } from "react";
import Card from "@/components/Card";
import LanguageBars from "@/components/LanguageBars";
import { ErrorPanel, LoadingPanel } from "@/components/LoadStates";
import StatTile from "@/components/StatTile";
import { useOverview } from "@/components/useOverview";
import { formatBytes, formatCount } from "@/lib/format";

export default function OverviewPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const { data, error } = useOverview(sessionId);

  if (error) return <ErrorPanel message={error.message} gone={error.status === 404} />;
  if (!data) return <LoadingPanel />;

  const { repository, scan } = data;
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
        <StatTile label="Entry points" value={formatCount(scan.entry_points.length)} />
        <StatTile label="Test files" value={formatCount(testCount)} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Languages">
          <LanguageBars languages={scan.languages} />
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

      <p className="mt-6 text-xs text-ink-3">
        Scan summary: {formatCount(scan.summary.total_files_seen)} files seen ·{" "}
        {formatCount(scan.summary.files_included)} included ·{" "}
        {formatCount(scan.summary.files_skipped_binary)} binary skipped ·{" "}
        {formatCount(scan.summary.dirs_skipped)} directories ignored
      </p>
    </div>
  );
}
