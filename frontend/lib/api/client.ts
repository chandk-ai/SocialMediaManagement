'use client';

/**
 * Thin API client. All requests hit /api/proxy/* which Next rewrites to the
 * backend. The session token is added by the proxy route handler so the
 * browser never holds it.
 */
import useSWR, { type SWRConfiguration } from 'swr';

const BASE = '/api/proxy';

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText} – ${body.slice(0, 200)}`);
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
  return useSWR<T>(path, (p: string) => api.get<T>(p), opts);
}
