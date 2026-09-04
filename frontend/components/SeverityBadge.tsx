import type { Severity } from "@/types/analysis";

/** Severity is encoded by label AND color, never color alone. */
const STYLES: Record<Severity, string> = {
  CRITICAL: "border-status-critical/60 bg-status-critical/15 text-status-critical",
  HIGH: "border-status-serious/60 bg-status-serious/15 text-status-serious",
  MEDIUM: "border-status-warning/60 bg-status-warning/15 text-status-warning",
  LOW: "border-accent/50 bg-accent/10 text-accent",
  INFO: "border-edge bg-raised text-ink-2",
};

export default function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-block rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wider ${STYLES[severity] ?? STYLES.INFO}`}
    >
      {severity}
    </span>
  );
}
