'use client';

/**
 * Top-level app-router error boundary.
 *
 * This catches errors the class-component ``ErrorBoundary`` can't —
 * specifically errors thrown during server-component rendering and
 * route-level transitions in Next.js 14. The class boundary still wraps
 * client-component subtrees; this is the belt-and-braces fallback for
 * everything else.
 *
 * The page renders three escape hatches so a stuck user always has a
 * way out:
 *   * **Try again** — Next's reset() reruns the failed segment.
 *   * **Reload** — hard reload, useful for stale auth tokens.
 *   * **Back to dashboard** — abandon the current route entirely.
 *
 * Sentry capture happens on mount; same pattern as the class boundary
 * so both surfaces produce a single, attributable event.
 */
import { useEffect } from 'react';
import Link from 'next/link';
import { AlertTriangle, Home, RefreshCcw, RotateCcw } from 'lucide-react';

export default function GlobalError({
  error, reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    void import('@sentry/nextjs')
      .then((Sentry) => {
        try {
          Sentry.captureException(error, {
            tags: { source: 'app-router-error' },
            extra: { digest: error.digest },
          });
        } catch {
          /* swallow — observability must never crash the app */
        }
      })
      .catch(() => { /* @sentry/nextjs not installed */ });
  }, [error]);

  return (
    <div className="min-h-screen flex items-center justify-center p-8">
      <div className="card max-w-md text-center">
        <div className="mx-auto mb-3 size-10 rounded-full bg-red-50 flex items-center justify-center text-red-600">
          <AlertTriangle size={20} />
        </div>
        <h1 className="text-base font-semibold mb-1">Something went wrong</h1>
        <p className="text-sm text-ink-500 mb-4">
          {error.message || 'An unexpected error occurred.'}
        </p>
        {error.digest && (
          <p className="text-xs text-ink-400 mb-4 font-mono">
            Error ID: {error.digest}
          </p>
        )}
        <div className="flex flex-wrap justify-center gap-2">
          <button onClick={reset} className="btn btn-primary inline-flex items-center gap-1">
            <RotateCcw size={14}/> Try again
          </button>
          <button onClick={() => location.reload()} className="btn btn-outline inline-flex items-center gap-1">
            <RefreshCcw size={14}/> Reload
          </button>
          <Link href="/dashboard" className="btn btn-ghost inline-flex items-center gap-1">
            <Home size={14}/> Back to dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
