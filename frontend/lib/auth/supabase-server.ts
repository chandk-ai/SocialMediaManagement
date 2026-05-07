/**
 * Supabase server-side client + middleware variant.
 * Used by middleware (route guards) and the API proxy route (token forwarding).
 */
import { createServerClient, type CookieOptions } from '@supabase/ssr';
import type { NextRequest, NextResponse } from 'next/server';
import { cookies } from 'next/headers';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

export function getServerSupabase() {
  const url  = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  const cookieStore = cookies();
  return createServerClient(url, anon, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (xs: CookieToSet[]) => {
        try {
          xs.forEach(({ name, value, options }) =>
            cookieStore.set({ name, value, ...(options ?? {}) }),
          );
        } catch {
          // `cookies().set()` only works inside Server Actions / Route
          // Handlers; in plain RSC reads we silently no-op.
        }
      },
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
      setAll: (xs: CookieToSet[]) => {
        xs.forEach(({ name, value, options }) => {
          req.cookies.set({ name, value, ...(options ?? {}) });
          res.cookies.set({ name, value, ...(options ?? {}) });
        });
      },
    },
  });
}
