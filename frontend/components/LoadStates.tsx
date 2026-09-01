import Link from "next/link";

export function LoadingPanel() {
  return (
    <div className="flex h-64 items-center justify-center gap-3 text-sm text-ink-3">
      <span className="h-2 w-2 rounded-full bg-accent [animation:pulse-soft_1.2s_ease-in-out_infinite]" />
      Loading analysis…
    </div>
  );
}

export function ErrorPanel({ message, gone }: { message: string; gone: boolean }) {
  return (
    <div className="mx-auto mt-24 max-w-md rounded-xl border border-edge bg-surface p-6 text-center">
      <h1 className="text-lg font-medium">
        {gone ? "Session not found" : "Could not load analysis"}
      </h1>
      <p className="mt-2 text-sm text-ink-2">
        {gone
          ? "This analysis session no longer exists - it may have been deleted."
          : message}
      </p>
      <Link
        href="/"
        className="mt-5 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:bg-accent/85"
      >
        Analyze a repository
      </Link>
    </div>
  );
}
