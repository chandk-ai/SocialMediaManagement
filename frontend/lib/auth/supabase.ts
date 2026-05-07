/**
 * Supabase auth client for the browser. The bearer token is the Supabase
 * access token; the proxy route (`/api/proxy/...`) forwards it to the
 * backend, which verifies it against SUPABASE_JWT_SECRET.
 */
'use client';
import { createBrowserClient } from '@supabase/ssr';

export function getSupabase() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  return createBrowserClient(url, anon);
}

export async function currentAccessToken(): Promise<string | null> {
  try {
    const sb = getSupabase();
    const { data } = await sb.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}
