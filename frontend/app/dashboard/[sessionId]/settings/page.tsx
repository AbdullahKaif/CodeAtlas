"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import Card from "@/components/Card";
import { ApiError, deleteSession } from "@/lib/api";
import { forgetSession } from "@/lib/sessions";

export default function SettingsPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onDelete() {
    setDeleting(true);
    setError(null);
    try {
      await deleteSession(sessionId);
      forgetSession(sessionId);
      router.push("/");
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        forgetSession(sessionId); // already gone - still a success for privacy
        router.push("/");
        return;
      }
      setError(err instanceof ApiError ? err.message : "Deletion failed. Try again.");
      setDeleting(false);
      setConfirming(false);
    }
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
