/**
 * Shared Playwright fixtures.
 *
 * The big one is `authedPage` — a page where the API client carries a
 * dev-mode bearer token (`dev.<role>.<email>.<org_id>`). The backend's
 * `get_current_user` short-circuits these tokens in dev/test envs (see
 * `app/core/security.py::_dev_principal`), so we don't need a real
 * Supabase user, an email server, or any out-of-band setup.
 *
 * The token format is parsed into Role + email + org_id by the backend.
 * Each org_id should be unique per spec to keep specs isolated; helpers
 * below mint a fresh UUID v4 so two specs running in parallel don't
 * stomp on each other's data.
 */
import { test as base, Page, expect } from '@playwright/test';

type Role = 'viewer' | 'editor' | 'admin';

export type AuthedFixtures = {
  authedPage: Page;
  /** Mint a fresh dev token for an arbitrary role/email/org. */
  mintDevToken: (opts?: { role?: Role; email?: string; orgId?: string }) => string;
};

/** Random UUID v4 — used to give each spec its own org_id. */
export function uuid(): string {
  // crypto.randomUUID() is available in Node 14.17+ and all browsers Playwright supports.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const c: any = (globalThis as any).crypto;
  if (c?.randomUUID) return c.randomUUID();
  // Fallback for older Node — RFC4122 v4 from Math.random.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    return (ch === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function buildDevToken(opts: { role?: Role; email?: string; orgId?: string } = {}): string {
  const role = opts.role ?? 'admin';
  const email = opts.email ?? 'e2e-admin@example.com';
  const orgId = opts.orgId ?? uuid();
  return `dev.${role}.${email}.${orgId}`;
}

export const test = base.extend<AuthedFixtures>({
  mintDevToken: async ({}, use) => {
    await use((opts = {}) => buildDevToken(opts));
  },

  authedPage: async ({ browser }, use) => {
    // Each test gets its own context so cookies / storage don't leak.
    const ctx = await browser.newContext();
    const page = await ctx.newPage();

    const token = buildDevToken();
    // Stash the token in localStorage under a key the API client knows
    // about. The frontend's `lib/api/client.ts` reads the supabase
    // session.access_token from supabase auth state; we shim that by
    // having every fetch include an Authorization header via
    // `context.setExtraHTTPHeaders`.
    await ctx.setExtraHTTPHeaders({ Authorization: `Bearer ${token}` });

    // Some pages also call `getSupabase().auth.getSession()`. We can't
    // forge a Supabase session client-side, but the API client tolerates
    // a missing session as long as the proxy route forwards the
    // Authorization header — which it does, see app/api/proxy/...
    // The middleware in middleware.ts also lets requests through when
    // `DEV_BEARER_TOKEN` is set (configured in playwright.config.ts).

    await use(page);
    await ctx.close();
  },
});

export { expect };

/** Wait for an SWR-driven endpoint to settle by watching for the network
 *  response. Useful when an API call must complete before assertions. */
export async function waitForApi(page: Page, pathSubstring: string): Promise<void> {
  await page.waitForResponse(
    (resp) => resp.url().includes(pathSubstring) && resp.ok(),
    { timeout: 10_000 },
  );
}
