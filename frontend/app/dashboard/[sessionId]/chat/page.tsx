"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import AnswerText from "@/components/AnswerText";
import EvidencePanel from "@/components/EvidencePanel";
import LLMSetupBanner from "@/components/LLMSetupBanner";
import { ApiError, askQuestion, getLLMHealth } from "@/lib/api";
import type { ChatAnswer, ChatMessage, LLMHealth } from "@/types/analysis";

type Turn =
  | { id: number; role: "user"; content: string }
  | { id: number; role: "assistant"; answer: ChatAnswer }
  | { id: number; role: "error"; content: string };

const EXAMPLE_QUESTIONS = [
  "How does authentication work?",
  "Where is the database connection initialized?",
  "What happens when a user logs in?",
  "Which files handle API requests?",
  "Explain the project architecture.",
  "Where are the main security risks?",
];

const HISTORY_LIMIT = 10;
const chatKey = (sessionId: string) => `codeatlas.chat.${sessionId}`;

function loadTurns(sessionId: string): Turn[] {
  try {
    const raw = sessionStorage.getItem(chatKey(sessionId));
    const parsed = raw ? (JSON.parse(raw) as Turn[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveTurns(sessionId: string, turns: Turn[]): void {
  try {
    sessionStorage.setItem(chatKey(sessionId), JSON.stringify(turns));
  } catch {
    /* storage unavailable - the conversation just will not survive navigation */
  }
}

function toHistory(turns: Turn[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  for (const turn of turns) {
    if (turn.role === "user") messages.push({ role: "user", content: turn.content });
    else if (turn.role === "assistant")
      messages.push({ role: "assistant", content: turn.answer.answer });
  }
  return messages.slice(-HISTORY_LIMIT);
}

export default function ChatPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [health, setHealth] = useState<LLMHealth | null>(null);
  const [checking, setChecking] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const checkHealth = useCallback(async () => {
    setChecking(true);
    try {
      setHealth(await getLLMHealth());
    } catch (err) {
      setHealth({
        reachable: false,
        base_url: "",
        model: "",
        model_available: false,
        available_models: [],
        ready: false,
        message:
          err instanceof ApiError ? err.message : "Could not check the local model status.",
      });
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Deferred so neither the sessionStorage read nor the health probe sets
    // state synchronously inside the effect (post-hydration only).
    Promise.resolve().then(() => {
      if (cancelled) return;
      setTurns(loadTurns(sessionId));
      setLoaded(true);
      checkHealth();
      // Other pages link here with ?q=... to prefill a question.
      try {
        const prefill = new URLSearchParams(window.location.search).get("q");
        if (prefill) {
          setInput(prefill);
          window.history.replaceState(null, "", window.location.pathname);
        }
      } catch {
        /* no window / malformed URL */
      }
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, checkHealth]);

  useEffect(() => {
    if (loaded) saveTurns(sessionId, turns);
  }, [sessionId, turns, loaded]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, pending]);

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || pending) return;
    const history = toHistory(turns);
    setTurns((prev) => [...prev, { id: Date.now(), role: "user", content: trimmed }]);
    setInput("");
    setPending(true);
    try {
      const answer = await askQuestion(sessionId, trimmed, history);
      setTurns((prev) => [...prev, { id: Date.now() + 1, role: "assistant", answer }]);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "The question could not be answered. Try again.";
      setTurns((prev) => [...prev, { id: Date.now() + 1, role: "error", content: message }]);
      if (err instanceof ApiError && err.status === 503) checkHealth();
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send(input);
    }
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-4rem)] max-w-4xl flex-col">
      <header className="mb-4">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">AI Chat</h1>
          {turns.length > 0 && (
            <button
              onClick={() => setTurns([])}
              className="rounded-lg border border-edge px-3 py-1.5 text-xs text-ink-2 transition hover:bg-raised"
            >
              Clear conversation
            </button>
          )}
        </div>
        <p className="mt-1 text-sm text-ink-2">
          Ask about this repository. Answers are grounded in retrieved code and every
          citation is validated against the analysis before it is shown.
        </p>
      </header>

      <div className="mb-4">
        <LLMSetupBanner health={health} onRecheck={checkHealth} checking={checking} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-edge bg-surface">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center p-8 text-center">
            <p className="text-sm text-ink-2">Try one of these to get started</p>
            <ul className="mt-4 flex max-w-xl flex-wrap justify-center gap-2">
              {EXAMPLE_QUESTIONS.map((q) => (
                <li key={q}>
                  <button
                    onClick={() => send(q)}
                    disabled={pending}
                    className="rounded-full border border-edge bg-raised px-3 py-1.5 text-xs text-ink-2 transition hover:border-accent/60 hover:text-ink disabled:opacity-50"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ol className="space-y-6 p-5">
            {turns.map((turn) => {
              if (turn.role === "user") {
                return (
                  <li key={turn.id} className="flex justify-end">
                    <p className="max-w-[80%] whitespace-pre-wrap rounded-xl bg-accent-soft px-4 py-2.5 text-sm text-ink">
                      {turn.content}
                    </p>
                  </li>
                );
              }
              if (turn.role === "error") {
                return (
                  <li key={turn.id} role="alert" className="max-w-[90%]">
                    <div className="rounded-xl border border-status-critical/40 bg-status-critical/10 px-4 py-3 text-sm text-ink-2">
                      {turn.content}
                    </div>
                  </li>
                );
              }
              return (
                <li key={turn.id} className="max-w-[95%]">
                  <div className="rounded-xl border border-edge bg-raised px-4 py-3">
                    <AnswerText text={turn.answer.answer} />
                    <EvidencePanel answer={turn.answer} />
                  </div>
                </li>
              );
            })}
            {pending && (
              <li className="flex items-center gap-3 text-sm text-ink-3">
                <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
                Retrieving code and asking the local model…
              </li>
            )}
            <div ref={bottomRef} />
          </ol>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="mt-4 flex gap-2 rounded-xl border border-edge bg-surface p-2 focus-within:border-accent/60"
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about this repository… (Enter to send, Shift+Enter for a new line)"
          rows={2}
          maxLength={2000}
          disabled={pending}
          aria-label="Question about the repository"
          className="w-full resize-none bg-transparent px-3 py-1.5 text-sm text-ink outline-none placeholder:text-ink-3 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={pending || !input.trim()}
          className="shrink-0 self-end rounded-lg bg-accent px-5 py-2.5 text-sm font-medium text-white transition hover:bg-accent/85 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {pending ? "Thinking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
