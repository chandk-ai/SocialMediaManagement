import { NextRequest, NextResponse } from 'next/server';
import { getServerSession } from 'next-auth';
import { authOptions } from '@/lib/auth/options';

const BACKEND = process.env.BACKEND_URL || 'http://localhost:8000';

export async function GET(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx);
}
export async function POST(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx);
}
export async function PUT(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx);
}
export async function DELETE(req: NextRequest, ctx: { params: { path: string[] } }) {
  return forward(req, ctx);
}

async function forward(req: NextRequest, { params }: { params: { path: string[] } }) {
  const session = (await getServerSession(authOptions)) as any;
  const token = session?.accessToken || process.env.DEV_BEARER_TOKEN || '';
  const upstream = `${BACKEND}/api/v1/${params.path.join('/')}${req.nextUrl.search}`;
  const headers: Record<string, string> = {
    'Content-Type': req.headers.get('content-type') ?? 'application/json',
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const init: RequestInit = { method: req.method, headers };
  if (req.method !== 'GET' && req.method !== 'HEAD') init.body = await req.text();
  const res = await fetch(upstream, init);
  return new NextResponse(res.body, { status: res.status, headers: res.headers });
}
