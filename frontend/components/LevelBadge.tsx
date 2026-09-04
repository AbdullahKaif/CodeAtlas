import type { ImpactLevel } from "@/types/analysis";

const STYLES: Record<ImpactLevel, string> = {
  HIGH: "border-status-critical/60 bg-status-critical/15 text-status-critical",
  MEDIUM: "border-status-warning/60 bg-status-warning/15 text-status-warning",
  LOW: "border-status-good/60 bg-status-good/15 text-status-good",
};

export default function LevelBadge({ level }: { level: ImpactLevel }) {
  return (
    <span className={`inline-block rounded-md border px-2 py-0.5 font-mono text-xs font-semibold tracking-wider ${STYLES[level]}`}>
      {level}
    </span>
  );
}
