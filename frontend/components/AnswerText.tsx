/**
 * Minimal renderer for model answers: fenced code blocks, bullet lists,
 * paragraphs and inline `code`. Deliberately not a Markdown engine - the
 * answer is plain text with light structure, and no extra dependency is
 * worth pulling in for it.
 */
type Segment = { kind: "code"; lang: string; body: string } | { kind: "text"; body: string };

function splitFences(text: string): Segment[] {
  const segments: Segment[] = [];
  const fence = /```([\w+-]*)\n([\s\S]*?)```/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = fence.exec(text)) !== null) {
    if (match.index > cursor) segments.push({ kind: "text", body: text.slice(cursor, match.index) });
    segments.push({ kind: "code", lang: match[1], body: match[2].replace(/\n$/, "") });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) segments.push({ kind: "text", body: text.slice(cursor) });
  return segments;
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("`") && part.endsWith("`") && part.length > 2 ? (
          <code key={i} className="rounded bg-raised px-1 py-0.5 font-mono text-[12px] text-ink">
            {part.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;

function Paragraphs({ body }: { body: string }) {
  const blocks = body.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (lines.length > 0 && lines.every((l) => BULLET.test(l))) {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {lines.map((line, j) => (
                <li key={j}>
                  <Inline text={line.replace(BULLET, "")} />
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            <Inline text={block} />
          </p>
        );
      })}
    </>
  );
}

export default function AnswerText({ text }: { text: string }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed text-ink-2">
      {splitFences(text).map((segment, i) =>
        segment.kind === "code" ? (
          <pre
            key={i}
            className="overflow-x-auto rounded-lg border border-edge bg-page p-3 font-mono text-xs leading-relaxed text-ink-2"
          >
            <code>{segment.body}</code>
          </pre>
        ) : (
          <Paragraphs key={i} body={segment.body} />
        ),
      )}
    </div>
  );
}
