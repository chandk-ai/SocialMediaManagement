/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    serverActions: { allowedOrigins: ['localhost:3000'] },
    // Loads ``frontend/instrumentation.ts`` once per server bootstrap.
    // Required by Sentry SDK 8 — this is what replaces the legacy
    // ``sentry.server.config.ts`` / ``sentry.edge.config.ts`` files.
    // Becomes default-on in Next 15; explicit here for clarity on 14.
    instrumentationHook: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/proxy/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/:path*`,
      },
    ];
  },
};

// ─── Sentry wrapper ─────────────────────────────────────────────────────────
// `withSentryConfig` adds source-map upload + automatic instrumentation. When
// SENTRY_DSN is unset we still wrap (cheap) — the SDK init guards on its own
// will skip dispatch. The `silent` option keeps build logs clean unless we
// explicitly opt in by setting SENTRY_DEBUG=true.
let exported = nextConfig;
try {
  // Dynamic import keeps the dev server bootable even if the package isn't
  // installed yet (e.g. during the initial `npm i` after pulling this change).
  const { withSentryConfig } = await import('@sentry/nextjs');
  exported = withSentryConfig(nextConfig, {
    silent: process.env.SENTRY_DEBUG !== 'true',
    org: process.env.SENTRY_ORG,
    project: process.env.SENTRY_PROJECT,
    authToken: process.env.SENTRY_AUTH_TOKEN,

    // Hide source maps from public clients — they're still uploaded
    // to Sentry for symbolicated stacks, just not served at /_next/.
    hideSourceMaps: true,

    // Widen the client upload to catch chunks Next code-splits behind
    // route boundaries; without this Sentry stacktraces miss frames in
    // dynamically-loaded pages.
    widenClientFileUpload: true,

    // Source-map handling — Sentry SDK 8 unified the options under a
    // nested ``sourcemaps`` block.
    sourcemaps: {
      // When no auth token is set we have no Sentry to upload to —
      // skip generation entirely so the build artifact stays small
      // and we don't ship orphan .map files.
      disable: !process.env.SENTRY_AUTH_TOKEN,
      // After successful upload, delete the .map files from the build
      // output so they don't end up in the deployed artifact. This
      // silences the "you may be serving Source Maps to your users"
      // warning the SDK now emits by default, and is the recommended
      // production posture.
      deleteSourcemapsAfterUpload: true,
    },

    // Skip the legacy webpack plugin entirely when there's no token —
    // the SDK still instruments client/server code via instrumentation.ts.
    disableServerWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
    disableClientWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
  });
} catch (err) {
  // @sentry/nextjs not installed yet — fall through with the plain config.
  // eslint-disable-next-line no-console
  console.warn('[next.config] @sentry/nextjs not available; skipping Sentry wrap.');
}

export default exported;
