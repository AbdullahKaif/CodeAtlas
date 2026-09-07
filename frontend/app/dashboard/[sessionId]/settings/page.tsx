"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import Card from "@/components/Card";
import { ApiError, deleteSession, getSessionFootprint } from "@/lib/api";
import { formatBytes, formatCount } from "@/lib/format";
import { forgetSession } from "@/lib/sessions";
import type { SessionFootprint } from "@/types/analysis";

type Deleted = { location: string; bytes: number; files: number; verified: boolean };

export default function SettingsPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const [footprint, setFootprint] = useState<SessionFootprint | null>(null);
  const [footprintError, setFootprintError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState<Deleted | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSessionFootprint(sessionId)
      .then((f) => !cancelled && setFootprint(f))
      .catch((err) => {
        if (!cancelled) setFootprintError(err instanceof ApiError ? err.message : "Could not read the session footprint.");
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  async function onDelete() {
    setDeleting(true);
    setError(null);
    const before = footprint;
    try {
      await deleteSession(sessionId);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) {
        setError(err instanceof ApiError ? err.message : "Deletion failed. Try again.");
        setDeleting(false);
        setConfirming(false);
        return;
      }
      // Already gone - still a success for privacy.
    }
    forgetSession(sessionId);
    // Verify by asking the backend again: a 404 is the proof that nothing is left.
    let verified = false;
    try {
      await getSessionFootprint(sessionId);
    } catch (err) {
      verified = err instanceof ApiError && err.status === 404;
    }
    setDeleted({
      location: before?.location ?? "the session directory",
      bytes: before?.total_bytes ?? 0,
      files: before?.total_files ?? 0,
      verified,
    });
    setDeleting(false);
  }

  if (deleted) {
    return (
      <div className="mx-auto max-w-2xl space-y-4">
        <h1 className="mb-6 text-xl font-semibold">Session deleted</h1>
        <Card title="What was removed">
          <p className="text-sm text-ink-2">
            {formatCount(deleted.files)} files ({formatBytes(deleted.bytes)}) under
          </p>
          <p className="mt-1 break-all font-mono text-xs text-ink-3">{deleted.location}</p>
          <p className="mt-4 flex items-center gap-2 text-sm">
            <span className={`h-2 w-2 rounded-full ${deleted.verified ? "bg-status-good" : "bg-status-warning"}`} />
            {deleted.verified
              ? "Verified: the backend no longer finds any data for this session."
              : "The backend did not confirm the deletion; check the directory above."}
          </p>
          <p className="mt-3 text-xs text-ink-3">
            The cloned repository, knowledge base, embeddings, security results and cached AI answers are gone.
            Nothing about this repository was stored anywhere else.
          </p>
          <Link href="/" className="mt-5 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent/85">
            Analyze another repository
          </Link>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="mb-6 text-xl font-semibold">Settings &amp; Privacy</h1>

      <Card title="Privacy">
        <dl className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <dt className="text-ink-2">Local processing</dt>
            <dd className="flex items-center gap-1.5 font-medium">
              <span className="h-1.5 w-1.5 rounded-full bg-status-good" />
              Enabled
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-ink-2">Repository retention</dt>
            <dd>Temporary session</dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-ink-2">Session id</dt>
            <dd className="font-mono text-xs text-ink-3">{sessionId}</dd>
          </div>
        </dl>
        <p className="mt-4 text-xs leading-relaxed text-ink-3">
          The cloned repository and every derived artifact live in an isolated
          folder in your system temp directory - outside cloud-synced folders.
          Nothing is sent to any external service beyond GitHub itself.
        </p>
      </Card>

      <Card title="What this session stores">
        {footprintError && <p className="text-sm text-ink-3">{footprintError}</p>}
        {!footprint && !footprintError && <p className="text-sm text-ink-3">Measuring…</p>}
        {footprint && (
          <>
            <p className="break-all font-mono text-xs text-ink-3">{footprint.location}</p>
            <table className="mt-3 w-full text-sm">
              <tbody>
                {footprint.artifacts.map((a) => (
                  <tr key={a.name} className="border-b border-line last:border-0">
                    <td className="py-1.5 pr-3 align-top">
                      <p className="font-mono text-xs text-ink">{a.name}</p>
                      <p className="text-[11px] text-ink-3">{a.description}</p>
                    </td>
                    <td className="whitespace-nowrap py-1.5 text-right align-top text-xs tabular-nums text-ink-3">
                      {formatCount(a.files)} files
                      <br />
                      {formatBytes(a.bytes)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-xs text-ink-2">
              {formatCount(footprint.total_files)} files, {formatBytes(footprint.total_bytes)} in total.{" "}
              <span className="text-ink-3">{footprint.note}</span>
            </p>
          </>
        )}
      </Card>

      <Card title="Danger zone">
        <p className="text-sm text-ink-2">
          Delete the cloned repository and all analysis data for this session.
          This cannot be undone.
        </p>
        {error && (
          <p role="alert" className="mt-3 text-sm text-status-critical">
            {error}
          </p>
        )}
        <div className="mt-4 flex items-center gap-3">
          {confirming ? (
            <>
              <button
                onClick={onDelete}
                disabled={deleting}
                className="rounded-lg bg-status-critical px-4 py-2 text-sm font-medium text-white transition hover:bg-status-critical/85 disabled:opacity-50"
              >
                {deleting ? "Deleting…" : "Yes, delete everything"}
              </button>
              <button
                onClick={() => setConfirming(false)}
                disabled={deleting}
                className="rounded-lg border border-edge px-4 py-2 text-sm text-ink-2 transition hover:bg-raised"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              className="rounded-lg border border-status-critical/50 px-4 py-2 text-sm font-medium text-status-critical transition hover:bg-status-critical/10"
            >
              Delete session data
            </button>
          )}
        </div>
      </Card>
    </div>
  );
}
