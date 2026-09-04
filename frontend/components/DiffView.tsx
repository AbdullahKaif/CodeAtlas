/** Renders a unified diff with per-line coloring; read-only, never applied. */
export default function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-edge bg-page p-3 font-mono text-[11px] leading-relaxed">
      {diff.split("\n").map((line, i) => {
        let cls = "text-ink-2";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "text-ink-3";
        else if (line.startsWith("@@")) cls = "text-accent";
        else if (line.startsWith("+")) cls = "bg-status-good/15 text-status-good";
        else if (line.startsWith("-")) cls = "bg-status-critical/15 text-status-critical";
        return (
          <div key={i} className={`px-1 ${cls}`}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
