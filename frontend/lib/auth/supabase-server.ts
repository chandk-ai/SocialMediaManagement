/**
 * Supabase server-side client + a small "current session" helper.
 * Used by middleware (route guards) and the API proxy route (token forwarding).
 */
import { createServerClient } from '@supabase/ssr';
import type { NextRequest, NextResponse } from 'next/server';
import { cookies } from 'next/headers';

export function getServerSupabase() {
  const url  = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  const cookieStore = cookies();
  return createServerClient(url, anon, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (xs) => xs.forEach(({ name, value, options }) =>
        cookieStore.set({ name, value, ...options })),
    },
  });
}

/** Middleware-compatible variant — pass the request, get a client + a response
 *  that carries any rotated session cookies back to the browser. */
export function getMiddlewareSupabase(req: NextRequest, res: NextResponse) {
  const url  = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  return createServerClient(url, anon, {
    cookies: {
      getAll: () => req.cookies.getAll(),
      setAll: (xs) => xs.forEach(({ name, value, options }) => {
        req.cookies.set({ name, value, ...options });
        res.cookies.set({ name, value, ...options });
      }),
    },
  });
}
