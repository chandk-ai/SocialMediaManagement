/**
 * Route guard — redirect unauthenticated users to /login.
 *
 * Recognises Supabase Auth sessions (cookie-based via @supabase/ssr) and
 * NextAuth sessions (legacy, only if Okta is enabled).
 */
import { NextResponse, type NextRequest } from 'next/server';
import { getMiddlewareSupabase } from '@/lib/auth/supabase-server';

const PUBLIC = [
  '/login', '/signup', '/no-access',
  '/api/auth',                  // NextAuth callback paths (Okta SSO)
  '/_next', '/favicon.ico',
  '/api/proxy/health',          // backend health proxy (anonymous OK)
];

export async function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (PUBLIC.some(p => path.startsWith(p))) return NextResponse.next();

  // Build a response we can attach rotated cookies to.
  const res = NextResponse.next();
  const supa = getMiddlewareSupabase(req, res);
  const { data: { user } } = await supa.auth.getUser();
  if (user) return res;

  // Optional NextAuth fallback (only matters when Okta is configured).
  try {
    const { getToken } = await import('next-auth/jwt');
    const tok = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });
    if (tok) return res;
  } catch { /* NextAuth not configured — fine */ }

  // Dev escape hatch — only outside production.
  if (process.env.NODE_ENV !== 'production' && process.env.DEV_BEARER_TOKEN) {
    return res;
  }

  const url = req.nextUrl.clone();
  url.pathname = '/login';
  url.searchParams.set('callbackUrl', path);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
