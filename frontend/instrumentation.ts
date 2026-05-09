/**
 * Next.js instrumentation hook — runs once per server bootstrap.
 *
 * This file replaces the legacy ``sentry.server.config.ts`` and
 * ``sentry.edge.config.ts`` files (Sentry SDK 8 + Next 14 deprecated
 * those in favour of the instrumentation hook so the SDK can register
 * itself before any user code runs).
 *
 * The browser-side init still lives in ``sentry.client.config.ts`` —
 * that file is loaded automatically via ``withSentryConfig`` and is
 * NOT replaced by this hook.
 *
 * Routing by runtime:
 *   * NEXT_RUNTIME === 'nodejs'  → server components / route handlers
 *   * NEXT_RUNTIME === 'edge'    → middleware.ts + edge route handlers
 *
 * Both call ``Sentry.init`` with the same DSN; environments and
 * release tags are read from env so a single Sentry project can hold
 * dev / staging / prod data without code changes.
 */
import type * as SentryNS from '@sentry/nextjs';

export async function register(): Promise<void> {
  const dsn = process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  const Sentry: typeof SentryNS = await import('@sentry/nextjs');

  const common = {
    dsn,
    environment:
      process.env.SENTRY_ENVIRONMENT
      || process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT
      || process.env.NODE_ENV,
    release:
      process.env.SENTRY_RELEASE
      || process.env.NEXT_PUBLIC_SENTRY_RELEASE,
    tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE ?? 0.1),
  };

  if (process.env.NEXT_RUNTIME === 'nodejs') {
    Sentry.init({
      ...common,
      // PII off by default — server-side data passing through includes
      // workflow content, auth headers, etc. Customers turn on per-org
      // via SENTRY_PII=true if they explicitly want it.
      sendDefaultPii: process.env.SENTRY_PII === 'true',
    });
  }

  if (process.env.NEXT_RUNTIME === 'edge') {
    // Edge runtime can't load the full Node SDK; @sentry/nextjs ships
    // the lighter variant automatically when imported under edge.
    Sentry.init(common);
  }
}
