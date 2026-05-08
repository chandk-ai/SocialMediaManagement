'use client';
/**
 * App Router global error boundary. Reports the error to Sentry and shows
 * a minimal fallback. This is the OUTERMOST boundary — it owns its own
 * <html>/<body> because layout.tsx itself may not have rendered.
 */
import { useEffect } from 'react';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    void import('@sentry/nextjs')
      .then((Sentry) => {
        try {
          Sentry.captureException(error);
        } catch { /* ignore */ }
      })
      .catch(() => { /* ignore */ });
  }, [error]);

  return (
    <html>
      <body style={{ fontFamily: 'system-ui, sans-serif', padding: 32 }}>
        <h1 style={{ fontSize: 18, fontWeight: 600 }}>Something went wrong</h1>
        <p style={{ marginTop: 8, color: '#555' }}>
          The page failed to render. The error has been reported.
        </p>
        <button
          onClick={() => reset()}
          style={{
            marginTop: 16, padding: '8px 14px', borderRadius: 8,
            background: '#111', color: 'white', border: 0, cursor: 'pointer',
          }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
