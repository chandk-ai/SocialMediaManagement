/**
 * Next.js middleware — guard authenticated routes.
 *
 * Anything not in the PUBLIC list is gated behind a NextAuth session token.
 * No session → redirect to /login with the target URL preserved.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { getToken } from 'next-auth/jwt';

const PUBLIC = [
  '/login', '/signup', '/onboarding/welcome',
  '/api/auth', '/_next', '/favicon.ico', '/api/proxy/health',
];

export async function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (PUBLIC.some(p => path.startsWith(p))) return NextResponse.next();

  const token = await getToken({
    req,
    secret: process.env.NEXTAUTH_SECRET,
  });
  // Allow dev token bypass in local mode
  const devBypass = process.env.DEV_BEARER_TOKEN && process.env.NODE_ENV !== 'production';
  if (token || devBypass) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = '/login';
  url.searchParams.set('callbackUrl', path);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
