'use client';

/**
 * Thin API client + a typed `useApi` SWR hook. Errors are surfaced two ways:
 * 1. `useApi` returns SWR's `error` object so pages can render a banner
 * 2. Imperative callers (`api.post`) get a typed `ApiError` they can catch
 *
 * Auth: we attach the Supabase access token directly from the browser-side
 * session (localStorage-backed). The proxy route forwards this header through
 * to FastAPI, which validates it against SUPABASE_JWT_SECRET. This avoids
 * any dependency on cookie-based session sync between @supabase/ssr and the
 * server route handler.
 */
import useSWR, { type SWRConfiguration } from 'swr';
import { getSupabase } from '@/lib/auth/supabase';

const BASE = '/api/proxy';

export class ApiError extends Error {
  status: number;
  detail: string;
  url: string;
  constructor(status: number, detail: string, url: string) {
    super(`${status} on ${url} — ${detail}`);
    this.status = status;
    this.detail = detail;
    this.url = url;
  }
}

async function getAccessToken(): Promise<string | null> {
  try {
    const supa = getSupabase();
    const { data } = await supa.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = `${BASE}${path}`;
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) || {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(url, {
    ...init,
    headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail || body?.message || JSON.stringify(body);
    } catch {
      try { detail = await res.text(); } catch { /* ignore */ }
    }
    throw new ApiError(res.status, detail || 'Unknown error', url);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get:    <T>(p: string) => request<T>(p),
  post:   <T>(p: string, body?: unknown) => request<T>(p, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  put:    <T>(p: string, body?: unknown) => request<T>(p, { method: 'PUT',  body: JSON.stringify(body ?? {}) }),
  del:    <T>(p: string) => request<T>(p, { method: 'DELETE' }),
};

export function useApi<T>(path: string | null, opts?: SWRConfiguration) {
  return useSWR<T, ApiError>(
    path,
    (p: string) => api.get<T>(p),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false,
      ...opts,
    },
  );
}
