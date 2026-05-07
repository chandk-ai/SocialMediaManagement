'use client';

/**
 * Thin API client + a typed `useApi` SWR hook. Errors are surfaced two ways:
 * 1. `useApi` returns SWR's `error` object so pages can render a banner
 * 2. Imperative callers (`api.post`) get a typed `ApiError` they can catch
 */
import useSWR, { type SWRConfiguration } from 'swr';

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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    ...init,
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
