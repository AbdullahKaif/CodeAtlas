"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV: { label: string; segment: string | null; soon?: boolean }[] = [
  { label: "Overview", segment: "" },
  { label: "Security", segment: null, soon: true },
  { label: "AI Chat", segment: "chat" },
  { label: "Architecture", segment: null, soon: true },
  { label: "Impact Analysis", segment: null, soon: true },
  { label: "Onboarding", segment: null, soon: true },
  { label: "Documentation", segment: null, soon: true },
  { label: "Settings", segment: "settings" },
];

export default function Sidebar({ sessionId }: { sessionId: string }) {
  const pathname = usePathname();
  const base = `/dashboard/${sessionId}`;

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-surface">
      <Link href="/" className="px-5 py-5">
        <span className="font-mono text-sm font-semibold tracking-[0.3em] text-accent">
          CODEATLAS
        </span>
      </Link>
      <nav className="flex-1 space-y-0.5 px-3">
        {NAV.map((item) => {
          if (item.segment === null) {
            return (
              <span
                key={item.label}
                aria-disabled="true"
                title="Coming in a later phase"
                className="flex cursor-not-allowed items-center justify-between rounded-lg px-3 py-2 text-sm text-ink-3/70"
              >
                {item.label}
                <span className="text-[9px] uppercase tracking-wider">soon</span>
              </span>
            );
          }
          const href = item.segment ? `${base}/${item.segment}` : base;
          const active = pathname === href;
          return (
            <Link
              key={item.label}
              href={href}
              className={`block rounded-lg px-3 py-2 text-sm transition ${
                active
                  ? "bg-accent-soft font-medium text-accent"
                  : "text-ink-2 hover:bg-raised hover:text-ink"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-line px-5 py-4">
        <p className="text-[11px] leading-relaxed text-ink-3">
          Local processing
          <span className="mx-1.5 inline-block h-1.5 w-1.5 rounded-full bg-status-good align-middle" />
          enabled
        </p>
      </div>
    </aside>
  );
}
