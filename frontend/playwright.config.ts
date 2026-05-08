/**
 * Playwright config — drives the existing Next.js dev server.
 *
 * Three pieces are configurable via env vars so CI and local runs share
 * the same config:
 *
 *   E2E_BASE_URL          — defaults to http://localhost:3000
 *                           Set this to your deployed preview URL to run
 *                           the same specs against staging.
 *   E2E_BACKEND_URL       — defaults to http://localhost:8000
 *                           The dev-token fixture mints tokens against
 *                           POST /api/v1/auth/dev-token, so this is needed
 *                           even when running against a deployed frontend.
 *   E2E_DEV_BEARER_TOKEN  — optional, lets specs skip token minting and
 *                           reuse a static dev-mode token.
 *
 * The `webServer` block boots the Next dev server when no E2E_BASE_URL is
 * set so local `npm run e2e` works with one command.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = 3000;
const BASE_URL = process.env.E2E_BASE_URL || `http://localhost:${PORT}`;
const RUN_LOCAL = !process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './e2e',
  // Each spec is independent — parallelise across files but serialise
  // within a file so the team-invite spec doesn't race with itself.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',

  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // The dev-token fixture sets DEV_BEARER_TOKEN as a localStorage shim
    // for the API client; setting it here too means raw `request.fetch`
    // calls still authenticate.
    extraHTTPHeaders: process.env.E2E_DEV_BEARER_TOKEN
      ? { Authorization: `Bearer ${process.env.E2E_DEV_BEARER_TOKEN}` }
      : undefined,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Boot Next.js automatically when running locally.
  webServer: RUN_LOCAL
    ? {
        command: 'npm run dev',
        port: PORT,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: {
          NODE_ENV: 'development',
          // Forces the proxy route to talk to the real backend even when
          // NEXT_PUBLIC_API_URL is unset.
          NEXT_PUBLIC_API_URL: process.env.E2E_BACKEND_URL || 'http://localhost:8000',
          // Mark the dev escape-hatch so middleware doesn't redirect to
          // /login. The fixture mints a real token for the API headers.
          DEV_BEARER_TOKEN: process.env.E2E_DEV_BEARER_TOKEN || 'dev.admin.e2e@example.com.00000000-0000-0000-0000-000000000001',
        },
      }
    : undefined,
});
