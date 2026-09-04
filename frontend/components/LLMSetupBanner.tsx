import type { LLMHealth } from "@/types/analysis";

export default function LLMSetupBanner({
  health,
  onRecheck,
  checking,
}: {
  health: LLMHealth | null;
  onRecheck: () => void;
  checking: boolean;
}) {
  if (health === null) {
    return <p className="text-xs text-ink-3">Checking the local model…</p>;
  }
  if (health.ready) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-ink-3">
        <span className="h-1.5 w-1.5 rounded-full bg-status-good" />
        Local model <span className="font-mono text-ink-2">{health.model}</span> ready via Ollama
      </p>
    );
  }
  const steps = health.reachable
    ? [`ollama pull ${health.model}`]
    : ["ollama serve", `ollama pull ${health.model}`];
  return (
    <div className="rounded-xl border border-status-warning/40 bg-status-warning/10 p-4 text-sm">
      <p className="font-medium text-ink">
        {health.reachable ? "Model not installed" : "Ollama is not running"}
      </p>
      <p className="mt-1 text-ink-2">{health.message}</p>
      <div className="mt-3 space-y-1.5">
        {!health.reachable && (
          <p className="text-xs text-ink-3">
            Install Ollama from{" "}
            <a href="https://ollama.com" target="_blank" rel="noreferrer" className="text-accent">
              ollama.com
            </a>
            , then in a terminal:
          </p>
        )}
        {steps.map((cmd) => (
          <pre key={cmd} className="rounded-md border border-edge bg-page px-3 py-1.5 font-mono text-xs text-ink-2">
            {cmd}
          </pre>
        ))}
      </div>
      <button
        onClick={onRecheck}
        disabled={checking}
        className="mt-3 rounded-lg border border-edge px-3 py-1.5 text-xs text-ink-2 transition hover:bg-raised disabled:opacity-50"
      >
        {checking ? "Checking…" : "Re-check"}
      </button>
    </div>
  );
}
