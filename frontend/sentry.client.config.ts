/**
 * Sentry — browser side. Loaded automatically by @sentry/nextjs on every
 * page. We keep PII off by default and tag user_id / org_id explicitly from
 * the Supabase session in app/layout.tsx.
 *
 * No-op behaviour: when NEXT_PUBLIC_SENTRY_DSN is unset, init() is skipped
 * entirely so dev/preview builds never accidentally send events.
 */
import * as Sentry from '@sentry/nextjs';

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || process.env.NODE_ENV,
    release: process.env.NEXT_PUBLIC_SENTRY_RELEASE,
    // Performance — 10% of transactions in prod is enough to get a useful
    // sample without blowing through the plan. Bump locally if debugging.
    tracesSampleRate: Number(process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? 0.1),
    // Session replay — only on errors so we don't capture every session.
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0.5,
    // We tag user_id/org_id explicitly from auth state; default PII off.
    sendDefaultPii: false,
    integrations: [
      Sentry.replayIntegration({
        maskAllText: true,
        blockAllMedia: true,
      }),
    ],
  });
}
