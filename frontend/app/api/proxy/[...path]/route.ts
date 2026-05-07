/**
 * Authenticated proxy to the FastAPI backend.
 *
 * Reads the Supabase access token from the user's session cookies (set by
 * @supabase/ssr at sign-in) and forwards it as `Authorization: Bearer …`.
 * The FastAPI backend verifies the JWT against SUPABASE_JWT_SECRET.
 *
 * Falls back to NextAuth if Okta is configured, then to DEV_BEARER_TOKEN
 * (only in non-production) so local dev "just works".
 */
import { NextRequest, NextResponse } from 'next/server';
import { getServerSupabase } from '@/lib/auth/supabase-server';

// Strip any trailing slash so we never produce `//api/v1/...`.
const BACKEND = (process.env.BACKEND_URL
              || process.env.NEXT_PUBLIC_API_URL
              || 'http://localhost:8000').replace(/\/+$/, '');

async function resolveBearerToken(req: NextRequest): Promise<string> {
  // 1. Inbound Authorization header (browser-attached Supabase token).
  //    This is the primary path — see frontend/lib/api/client.ts.
  const inbound = req.headers.get('authorization');
  if (inbound && /^Bearer\s+\S+/i.test(inbound)) {
    return inbound.replace(/^Bearer\s+/i, '').trim();
  }

  // 2. Supabase session via @supabase/ssr cookies (fallback if cookies sync).
  try {
    const supa = getServerSupabase();
    const { data } = await supa.auth.getSession();
    if (data.session?.access_token) return data.session.access_token;
  } catch { /* not configured */ }

  // 3. NextAuth (Okta)
  try {
    const { getToken } = await import('next-auth/jwt');
    const tok = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });
    const at = (tok as any)?.accessToken;
    if (typeof at === 'string') return at;
  } catch { /* not configured */ }

  // 4. Dev fallback
  return process.env.DEV_BEARER_TOKEN || '';
}

async function forward(req: NextRequest, params: { path: string[] }) {
  const token = await resolveBearerToken(req);
  const upstream = `${BACKEND}/api/v1/${params.path.join('/')}${req.nextUrl.search}`;
  const headers: Record<string, string> = {
    'Content-Type': req.headers.get('content-type') ?? 'application/json',
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const init: RequestInit = { method: req.method, headers };
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    init.body = await req.text();
  }
  const res = await fetch(upstream, init);
  return new NextResponse(res.body, { status: res.status, headers: res.headers });
}

export async function GET(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params);
}
export async function POST(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params);
}
export async function PUT(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params);
}
export async function PATCH(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params);
}
export async function DELETE(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx.params);
}
