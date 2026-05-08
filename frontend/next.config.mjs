/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: { serverActions: { allowedOrigins: ['localhost:3000'] } },
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
    // Don't fail the build if source-map upload fails — Render builds don't
    // always have network egress to Sentry, and we still want the app to ship.
    disableServerWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
    disableClientWebpackPlugin: !process.env.SENTRY_AUTH_TOKEN,
    hideSourceMaps: true,
    widenClientFileUpload: true,
  });
} catch (err) {
  // @sentry/nextjs not installed yet — fall through with the plain config.
  // eslint-disable-next-line no-console
  console.warn('[next.config] @sentry/nextjs not available; skipping Sentry wrap.');
}

export default exported;
