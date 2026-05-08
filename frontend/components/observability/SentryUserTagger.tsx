'use client';
/**
 * Tags Sentry events with user_id / org_id so any error in the browser
 * surfaces alongside the affected tenant. We intentionally do NOT send
 * email or name — those are PII we keep out of crash reports.
 *
 * Mounted once in the root <Providers> tree; subscribes to Supabase auth
 * state changes so tags update on login/logout. No-ops when Sentry isn't
 * loaded (e.g. when NEXT_PUBLIC_SENTRY_DSN is unset and the SDK init
 * skipped — `setUser` still works on the package, just goes nowhere).
 */
import { useEffect } from 'react';
import { getSupabase } from '@/lib/auth/supabase';

export function SentryUserTagger() {
  useEffect(() => {
    let cancelled = false;
    let sub: { unsubscribe?: () => void } | null = null;

    (async () => {
      let Sentry: typeof import('@sentry/nextjs') | null = null;
      try {
        Sentry = await import('@sentry/nextjs');
      } catch {
        return; // package missing — silently skip
      }
      if (cancelled || !Sentry) return;

      const apply = (session: { user?: { id?: string }; access_token?: string } | null) => {
        if (!Sentry) return;
        const id = session?.user?.id;
        if (!id) {
          Sentry.setUser(null);
          Sentry.setTag('org_id', null as unknown as string);
          return;
        }
        Sentry.setUser({ id });
        // Pull org_id out of the JWT claims if we can — the backend's auth
        // middleware uses the same field name. Decoding is best-effort.
        const orgId = readOrgIdFromJwt(session?.access_token);
        if (orgId) Sentry.setTag('org_id', orgId);
      };

      try {
        const sb = getSupabase();
        const { data } = await sb.auth.getSession();
        apply(data.session);
        const { data: listener } = sb.auth.onAuthStateChange((_evt, session) => {
          apply(session);
        });
        sub = listener?.subscription ?? null;
      } catch {
        /* ignore — auth may not be configured in dev */
      }
    })();

    return () => {
      cancelled = true;
      try { sub?.unsubscribe?.(); } catch { /* ignore */ }
    };
  }, []);

  return null;
}

function readOrgIdFromJwt(token: string | undefined): string | null {
  if (!token) return null;
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    return payload?.org_id || payload?.['https://smms/org_id'] || payload?.app_metadata?.org_id || null;
  } catch {
    return null;
  }
}
