"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AnswerText from "@/components/AnswerText";
import Card from "@/components/Card";
import EntityPicker from "@/components/EntityPicker";
import EvidencePanel from "@/components/EvidencePanel";
import LLMSetupBanner from "@/components/LLMSetupBanner";
import { ErrorPanel, LoadingPanel } from "@/components/LoadStates";
import {
  ApiError,
  generateDocumentation,
  generateTests,
  getDocumentationStatus,
  getLLMHealth,
  getTestingConventions,
} from "@/lib/api";
import type {
  ChatAnswer,
  DocumentationKind,
  DocumentationStatus,
  GeneratedDocumentation,
  LLMHealth,
  SourceReference,
  RetrievedChunk,
  TestingConventions,
  TestSuggestion,
} from "@/types/analysis";

type Async<T> = { state: "idle" } | { state: "loading" } | { state: "error"; message: string } | { state: "done"; value: T };

const TEST_TYPES = ["function", "method"];

/** EvidencePanel takes a chat answer; every AI result here carries the same evidence fields. */
function asAnswer(
  sessionId: string,
  text: string,
  r: { sources: SourceReference[]; context: RetrievedChunk[]; references_removed: number; model: string; duration_seconds: number },
): ChatAnswer {
  return {
    session_id: sessionId,
    question: "",
    answer: text,
    sources: r.sources,
    context: r.context,
    references_removed: r.references_removed,
    model: r.model,
    duration_seconds: r.duration_seconds,
  };
}

function download(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="rounded-lg border border-edge px-3 py-1.5 text-xs text-ink-2 transition hover:bg-raised"
    >
      {copied ? "Copied" : label}
    </button>
  );
}

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

export default function DocumentationPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [status, setStatus] = useState<DocumentationStatus | null>(null);
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const [health, setHealth] = useState<LLMHealth | null>(null);
  const [checking, setChecking] = useState(false);

  const [kind, setKind] = useState<DocumentationKind>("readme");
  const [doc, setDoc] = useState<Async<GeneratedDocumentation>>({ state: "idle" });
  const [view, setView] = useState<"rendered" | "markdown">("rendered");

  const [conventions, setConventions] = useState<TestingConventions | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [tests, setTests] = useState<Async<TestSuggestion>>({ state: "idle" });

  const checkHealth = useCallback(async () => {
    setChecking(true);
    try {
      setHealth(await getLLMHealth());
    } catch {
      setHealth(null);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getDocumentationStatus(sessionId)
      .then((s) => !cancelled && setStatus(s))
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof ApiError ? { message: err.message, status: err.status } : { message: "Failed to load.", status: -1 });
      });
    getTestingConventions(sessionId)
      .then((c) => !cancelled && setConventions(c))
      .catch(() => undefined);
    Promise.resolve().then(() => {
      if (cancelled) return;
      checkHealth();
      try {
        const initial = new URLSearchParams(window.location.search).get("target");
        if (initial) setTarget(initial);
      } catch {
        /* ignore */
      }
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, checkHealth]);

  const llmReady = health?.ready ?? false;

  async function draft(refresh = false) {
    setDoc({ state: "loading" });
    try {
      const value = await generateDocumentation(sessionId, kind, refresh);
      setDoc({ state: "done", value });
      setStatus((s) => (s ? { ...s, kinds: s.kinds.map((k) => (k.kind === kind ? { ...k, cached: true } : k)) } : s));
    } catch (err) {
      setDoc({ state: "error", message: err instanceof ApiError ? err.message : "Drafting failed." });
    }
  }

  async function suggest(refresh = false) {
    if (!target) return;
    setTests({ state: "loading" });
    try {
      setTests({ state: "done", value: await generateTests(sessionId, target, refresh) });
    } catch (err) {
      setTests({ state: "error", message: err instanceof ApiError ? err.message : "Suggestion failed." });
    }
  }

  if (error) return <ErrorPanel message={error.message} gone={error.status === 404} />;
  if (!status) return <LoadingPanel />;

  const selected = status.kinds.find((k) => k.kind === kind);
  const docReady = doc.state === "done" && doc.value.kind === kind ? doc.value : null;

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-5">
        <h1 className="text-xl font-semibold">Documentation &amp; Tests</h1>
        <p className="mt-1 text-sm text-ink-2">
          Draft documentation and suggest tests for <span className="font-mono">{status.repository}</span> from
          its own code. Everything here is a suggestion to copy or download: nothing is written to the
          repository, and existing documentation is never overwritten.
        </p>
      </header>

      <div className="mb-4">
        <LLMSetupBanner health={health} onRecheck={checkHealth} checking={checking} />
      </div>

      <Card
        title="Documentation drafts"
        action={
          docReady ? (
            <button onClick={() => draft(true)} disabled={doc.state === "loading"} className="text-xs text-ink-3 hover:text-ink">
              Regenerate
            </button>
          ) : (
            <button
              onClick={() => draft()}
              disabled={doc.state === "loading" || !llmReady}
              title={llmReady ? undefined : "Set up Ollama to draft documentation"}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {doc.state === "loading" ? "Drafting…" : `Draft ${selected?.title ?? ""}`}
            </button>
          )
        }
      >
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {status.kinds.map((k) => (
            <button
              key={k.kind}
              onClick={() => {
                setKind(k.kind);
                if (doc.state === "done" && doc.value.kind !== k.kind) setDoc({ state: "idle" });
              }}
              aria-pressed={k.kind === kind}
              className={`rounded-lg border px-3 py-2 text-left transition ${
                k.kind === kind ? "border-accent/70 bg-accent-soft" : "border-edge bg-raised hover:border-accent/40"
              }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium text-ink">{k.title}</span>
                {k.cached && <span className="text-[10px] uppercase tracking-wider text-ink-3">drafted</span>}
              </div>
              <p className="mt-0.5 text-[11px] leading-snug text-ink-3">{k.description}</p>
              <p className="mt-1 font-mono text-[10px] text-ink-3">{k.filename}</p>
            </button>
          ))}
        </div>

        <p className="mt-3 text-xs text-ink-3">
          {status.existing_documents.length > 0 ? (
            <>
              Existing documentation found:{" "}
              {status.existing_documents.map((d, i) => (
                <span key={d.path}>
                  {i > 0 && ", "}
                  <span className="font-mono text-ink-2">{d.path}</span>
                </span>
              ))}
              . A draft with the same name is offered under a <span className="font-mono">.codeatlas.md</span> suffix.
            </>
          ) : (
            "No documentation files were found in the repository."
          )}
        </p>

        {doc.state === "error" && (
          <p role="alert" className="mt-3 rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-xs text-ink-2">{doc.message}</p>
        )}
        {doc.state === "loading" && (
          <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
            <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
            Building the fact sheet, retrieving code, asking the local model…
          </p>
        )}
        {docReady && (
          <div className="mt-4 rounded-lg border border-edge bg-raised p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[10px] uppercase tracking-wider text-ink-3">
                {docReady.title} · suggested file <span className="font-mono normal-case text-ink-2">{docReady.filename}</span>
                {docReady.cached && " · cached"}
              </p>
              <div className="flex items-center gap-2">
                <div className="seg flex overflow-hidden rounded-lg border border-edge text-xs">
                  {(["rendered", "markdown"] as const).map((v) => (
                    <button
                      key={v}
                      onClick={() => setView(v)}
                      aria-pressed={view === v}
                      className={`px-2.5 py-1 transition ${view === v ? "bg-accent-soft text-accent" : "text-ink-3 hover:text-ink"}`}
                    >
                      {v === "rendered" ? "Rendered" : "Markdown"}
                    </button>
                  ))}
                </div>
                <CopyButton text={docReady.markdown} />
                <button
                  onClick={() => download(docReady.filename, docReady.markdown)}
                  className="rounded-lg border border-edge px-3 py-1.5 text-xs text-ink-2 transition hover:bg-raised"
                >
                  Download
                </button>
              </div>
            </div>
            {!docReady.retrieval_used && (
              <p className="mt-2 text-[11px] text-status-warning">
                No vector index was available for this session, so the draft rests on the structural fact sheet
                and entity code only.
              </p>
            )}
            <div className="mt-3">
              {view === "rendered" ? (
                <AnswerText text={docReady.markdown} />
              ) : (
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-edge bg-page p-3 font-mono text-xs leading-relaxed text-ink-2">
                  {docReady.markdown}
                </pre>
              )}
            </div>
            <EvidencePanel answer={asAnswer(sessionId, docReady.markdown, docReady)} />
            <p className="mt-2 text-[11px] text-ink-3">{docReady.note}</p>
          </div>
        )}
      </Card>

      <div className="mt-4">
        <Card
          title="Test suggestions"
          action={
            tests.state === "done" ? (
              <button onClick={() => suggest(true)} className="text-xs text-ink-3 hover:text-ink">Regenerate</button>
            ) : (
              <button
                onClick={() => suggest()}
                disabled={!target || tests.state === "loading" || !llmReady}
                title={llmReady ? (target ? undefined : "Pick a function or method first") : "Set up Ollama to suggest tests"}
                className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {tests.state === "loading" ? "Suggesting…" : "Suggest tests"}
              </button>
            )
          }
        >
          {conventions && (
            <p className="mb-3 text-xs text-ink-3">
              Detected conventions:{" "}
              <span className="text-ink-2">
                {conventions.framework === "unknown" ? "no framework recognised" : conventions.framework}
              </span>
              {" · "}
              {conventions.test_files} test file{conventions.test_files === 1 ? "" : "s"}
              {conventions.test_directories.length > 0 && (
                <>
                  {" under "}
                  <span className="font-mono text-ink-2">{conventions.test_directories.join(", ")}</span>
                </>
              )}
              {conventions.naming_pattern && (
                <>
                  {" · naming "}
                  <span className="font-mono text-ink-2">{conventions.naming_pattern}</span>
                </>
              )}
              {conventions.fixtures_file && (
                <>
                  {" · fixtures in "}
                  <span className="font-mono text-ink-2">{conventions.fixtures_file}</span>
                </>
              )}
            </p>
          )}

          <EntityPicker
            sessionId={sessionId}
            types={TEST_TYPES}
            placeholder="Search a function or method to test…"
            onPick={(e) => {
              setTarget(e.id);
              setTests({ state: "idle" });
            }}
          />
          {target && (
            <p className="mt-2 text-xs text-ink-3">
              Target: <span className="font-mono text-ink">{target}</span>
              {" · "}
              <Link href={`/dashboard/${sessionId}/impact?target=${encodeURIComponent(target)}`} className="text-accent hover:underline">
                impact
              </Link>
            </p>
          )}

          {tests.state === "error" && (
            <p role="alert" className="mt-3 rounded-lg border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-xs text-ink-2">{tests.message}</p>
          )}
          {tests.state === "loading" && (
            <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
              <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
              Collecting the target, its callers and callees, existing tests, asking the local model…
            </p>
          )}
          {tests.state === "done" && (
            <div className="mt-4 rounded-lg border border-edge bg-raised p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[10px] uppercase tracking-wider text-ink-3">
                  {tests.value.suggested_file_exists ? "Add to" : "New file"}{" "}
                  <span className="font-mono normal-case text-ink-2">{tests.value.suggested_file}</span>
                  {tests.value.cached && " · cached"}
                </p>
                {tests.value.code && (
                  <div className="flex items-center gap-2">
                    <CopyButton text={tests.value.code} label="Copy code" />
                    <button
                      onClick={() => download(basename(tests.value.suggested_file), tests.value.code)}
                      className="rounded-lg border border-edge px-3 py-1.5 text-xs text-ink-2 transition hover:bg-raised"
                    >
                      Download
                    </button>
                  </div>
                )}
              </div>

              <ul className="mt-3 flex flex-wrap gap-1.5">
                {tests.value.categories.map((c) => (
                  <li
                    key={c.name}
                    title={c.tests.join(", ") || "not covered"}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] ${
                      c.covered ? "border-status-good/50 text-ink-2" : "border-edge text-ink-3 line-through"
                    }`}
                  >
                    {c.label}
                    {c.covered && <span className="ml-1 text-ink-3">{c.tests.length}</span>}
                  </li>
                ))}
              </ul>

              {tests.value.explanation && (
                <p className="mt-3 text-sm text-ink-2">{tests.value.explanation}</p>
              )}
              {tests.value.existing_tests.length > 0 && (
                <p className="mt-2 text-[11px] text-ink-3">
                  Already exercised by:{" "}
                  {tests.value.existing_tests.map((t, i) => (
                    <span key={t}>
                      {i > 0 && ", "}
                      <span className="font-mono text-ink-2">{t}</span>
                    </span>
                  ))}
                </p>
              )}

              {tests.value.code ? (
                <pre className="mt-3 overflow-x-auto rounded-lg border border-edge bg-page p-3 font-mono text-xs leading-relaxed text-ink-2">
                  <code>{tests.value.code}</code>
                </pre>
              ) : (
                <p className="mt-3 text-xs text-status-warning">The model returned no code block. Regenerate, or try a smaller target.</p>
              )}

              {tests.value.assumptions && (
                <div className="mt-3">
                  <p className="text-[10px] uppercase tracking-wider text-ink-3">Assumptions</p>
                  <AnswerText text={tests.value.assumptions} />
                </div>
              )}
              <EvidencePanel answer={asAnswer(sessionId, tests.value.explanation, tests.value)} />
              <p className="mt-2 text-[11px] text-ink-3">{tests.value.disclaimer}</p>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
