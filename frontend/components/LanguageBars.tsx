/**
 * Language share as a labeled horizontal bar list.
 * Categorical slots are assigned in fixed order of descending file count and
 * never cycled; languages beyond slot 8 fold into "Other" (muted, no hue).
 * Every row is direct-labeled, so color never carries identity alone.
 */
const SLOT_CLASSES = [
  "bg-series-1",
  "bg-series-2",
  "bg-series-3",
  "bg-series-4",
  "bg-series-5",
  "bg-series-6",
  "bg-series-7",
  "bg-series-8",
] as const;

export default function LanguageBars({
  languages,
}: {
  languages: Record<string, number>;
}) {
  const entries = Object.entries(languages).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  if (total === 0) return <p className="text-sm text-ink-3">No recognized languages.</p>;

  const top = entries.slice(0, 8);
  const otherCount = entries.slice(8).reduce((sum, [, n]) => sum + n, 0);
  const rows: { name: string; count: number; slot: number | null }[] = top.map(
    ([name, count], i) => ({ name, count, slot: i }),
  );
  if (otherCount > 0) rows.push({ name: "other", count: otherCount, slot: null });

  const max = Math.max(...rows.map((r) => r.count));

  return (
    <ul className="space-y-2.5" role="list">
      {rows.map((row) => (
        <li key={row.name} className="grid grid-cols-[7rem_1fr_5.5rem] items-center gap-3">
          <span className="truncate font-mono text-xs text-ink-2">{row.name}</span>
          <div className="h-2.5">
            <div
              className={`h-full rounded-r-[4px] ${
                row.slot === null ? "bg-ink-3/40" : SLOT_CLASSES[row.slot]
              }`}
              style={{ width: `${Math.max((row.count / max) * 100, 2)}%` }}
            />
          </div>
          <span className="text-right text-xs tabular-nums text-ink-3">
            {row.count} · {((row.count / total) * 100).toFixed(0)}%
          </span>
        </li>
      ))}
    </ul>
  );
}
