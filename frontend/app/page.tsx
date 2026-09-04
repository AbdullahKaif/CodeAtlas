"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { analyzeRepository, ApiError, getAnalysisStatus, getOverview } from "@/lib/api";
import { listSessions, rememberSession } from "@/lib/sessions";
import type { AnalysisStatus, RecentSession } from "@/types/analysis";

const STAGE_LABELS: Record<string, string> = {
  cloning: "Cloning the repository",
  scanning: "Scanning files",
  parsing: "Parsing source code",
  chunking: "Chunking for retrieval",
  embedding: "Embedding chunks (local model)",
  indexing: "Building the search index",
  security: "Scanning for vulnerabilities and secrets",
};

function describeProgress(status: AnalysisStatus): string {
  const running = status.stages.find((s) => s.state === "running");
  if (!running) return "Finishing up";
  const label = STAGE_LABELS[running.name] ?? running.name;
  return running.detail ? `${label} — ${running.detail}` : label;
}

const FEATURES: { title: string; text: string; soon?: boolean }[] = [
  { title: "Repository X-ray", text: "Clone and inventory any public GitHub repo: languages, entry points, structure." },
  { title: "Privacy first", text: "Everything runs on your machine. One click deletes every trace." },
  { title: "Grounded AI chat", text: "Ask how the code works. Answers come from a local model and cite verified file and line references." },
  { title: "Security scanning", text: "Semgrep + Gitleaks findings, secrets redacted, with AI explanations and fix suggestions." },
  { title: "Architecture & impact", text: "Interactive dependency graph and change-impact analysis.", soon: true },
];

export default function LandingPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<RecentSession[]>([]);

  useEffect(() => {
    let cancelled = false;
    // Deferred so the localStorage read never races hydration.
    Promise.resolve().then(() => {
      if (!cancelled) setRecent(listSessions());
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  async function onAnalyze(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setElapsed(0);
    setStage(null);
    setError(null);
    try {
      const started = await analyzeRepository(url.trim());
      // Analysis runs server-side in the background; poll the real per-stage
      // progress until it settles.
      let status = await getAnalysisStatus(started.session_id);
      while (status.state === "running") {
        setStage(describeProgress(status));
        await new Promise((resolve) => setTimeout(resolve, 1500));
        status = await getAnalysisStatus(started.session_id);
      }
      if (status.state === "failed") {
        setError(status.error ?? "Analysis failed.");
        setBusy(false);
        return;
      }
      const overview = await getOverview(started.session_id);
      rememberSession(overview);
      router.push(`/dashboard/${overview.session_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center px-6 py-16">
      <p className="font-mono text-xs tracking-[0.5em] text-accent">CODEATLAS</p>
      <h1 className="mt-4 text-center text-4xl font-semibold tracking-tight sm:text-5xl">
        Understand. Secure. Onboard.
      </h1>
      <p className="mt-4 max-w-xl text-center text-ink-2">
        Paste a GitHub repository and get a local, private map of what it is and
        how it fits together. Your code never leaves your machine.
      </p>

      <form onSubmit={onAnalyze} className="mt-10 w-full max-w-xl">
        <div className="flex gap-2 rounded-xl border border-edge bg-surface p-2 focus-within:border-accent/60">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repository"
            spellCheck={false}
            disabled={busy}
            aria-label="GitHub repository URL"
            className="w-full bg-transparent px-3 font-mono text-sm text-ink outline-none placeholder:text-ink-3 disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={busy || !url.trim()}
            className="shrink-0 rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Analyzing…" : "Analyze"}
          </button>
        </div>

        {busy && (
          <div className="mt-4 flex items-center gap-3 rounded-lg border border-edge bg-surface px-4 py-3 text-sm text-ink-2">
            <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
            {stage ?? "Starting the analysis"}
            {elapsed >= 5 && ` (${elapsed}s)`}
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="mt-4 rounded-lg border border-status-critical/40 bg-status-critical/10 px-4 py-3 text-sm text-ink-2"
          >
            {error}
          </div>
        )}
      </form>

      {recent.length > 0 && !busy && (
        <section className="mt-10 w-full max-w-xl">
          <h2 className="text-xs font-medium uppercase tracking-wider text-ink-3">
            Recent analyses
          </h2>
          <ul className="mt-2 divide-y divide-line overflow-hidden rounded-xl border border-edge bg-surface">
            {recent.map((s) => (
              <li key={s.sessionId}>
                <button
                  onClick={() => router.push(`/dashboard/${s.sessionId}`)}
                  className="flex w-full items-center justify-between px-4 py-3 text-left transition hover:bg-raised"
                >
                  <span className="font-mono text-sm">{s.name}</span>
                  <span className="text-xs text-ink-3">
                    {new Date(s.analyzedAt).toLocaleString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-14 grid w-full grid-cols-1 gap-3 sm:grid-cols-2">
        {FEATURES.map((f) => (
          <div key={f.title} className="rounded-xl border border-edge bg-surface p-4">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium">{f.title}</h3>
              {f.soon && (
                <span className="rounded-full border border-edge px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-3">
                  soon
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-ink-2">{f.text}</p>
          </div>
        ))}
      </section>

      <p className="mt-12 text-xs text-ink-3">
        Local processing · no code leaves your machine · sessions are deletable
      </p>
    </main>
  );
}
