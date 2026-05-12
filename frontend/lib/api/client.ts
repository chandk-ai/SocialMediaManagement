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

/**
 * Coerce any error-body shape into a human-readable string. FastAPI's 422
 * validation responses are arrays of objects (`[{type, loc, msg, input}]`);
 * naively rendering them in JSX crashes React with "Objects are not valid as
 * a React child". This flattens every shape we've seen into one string.
 */
function formatDetail(body: unknown): string {
  if (body == null) return '';
  if (typeof body === 'string') return body;
  if (typeof body === 'object') {
    const b = body as Record<string, unknown>;
    if (typeof b.message === 'string') return b.message;
    const detail = b.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      // Pydantic v2 validation error array → "field: msg; field: msg"
      return detail
        .map((e) => {
          const obj = e as Record<string, unknown>;
          const loc = Array.isArray(obj.loc) ? obj.loc.slice(1).join('.') : '';
          const msg = typeof obj.msg === 'string' ? obj.msg : JSON.stringify(obj);
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join('; ');
    }
    if (detail && typeof detail === 'object') return JSON.stringify(detail);
    try { return JSON.stringify(b); } catch { /* fall through */ }
  }
  return String(body);
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
    let code: string | undefined;
    try {
      const body = await res.json();
      // Pull out a structured `code` if the backend sent one (used today
      // for "no_membership" — see app/core/security.py). Plain detail
      // strings still flow through formatDetail() unchanged.
      const innerDetail =
        body && typeof body === 'object' ? (body as { detail?: unknown }).detail : undefined;
      if (innerDetail && typeof innerDetail === 'object' && 'code' in (innerDetail as object)) {
        code = String((innerDetail as { code?: unknown }).code ?? '');
      }
      detail = formatDetail(body) || res.statusText;
    } catch {
      try { detail = await res.text(); } catch { /* ignore */ }
    }
    // 403 + no_membership → bounce to the dedicated landing page so the
    // signed-in-but-unaffiliated user sees something useful instead of a
    // generic error banner. Done here because middleware can't intercept
    // proxy responses, and we need to catch this everywhere the API client
    // is called (every page).
    if (
      res.status === 403 && code === 'no_membership'
      && typeof window !== 'undefined'
      && !window.location.pathname.startsWith('/no-access')
    ) {
      window.location.replace('/no-access');
    }
    throw new ApiError(res.status, detail || 'Unknown error', url);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// Direct backend URL used by multipart uploads ONLY. Vercel serverless
// functions cap request bodies at 4.5 MB (Hobby plan) and even smaller
// on free tier — every regular API call routes through /api/proxy which
// re-buffers the body in the function, hitting that ceiling. Multipart
// file uploads above ~4 MB therefore have to skip the proxy and POST
// straight to the Render backend, which has a 500 MB ceiling.
//
// Set ``NEXT_PUBLIC_BACKEND_URL`` in Vercel → it's exposed to the
// browser at build time so the upload knows where to go. The token
// still travels in the Authorization header so the backend can verify
// it against SUPABASE_JWT_SECRET; the FastAPI CORSMiddleware already
// allows the frontend's origin.
const DIRECT_BACKEND = (
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_BACKEND_URL)
    || ''
).replace(/\/+$/, '');

async function requestForm<T>(path: string, form: FormData): Promise<T> {
  // Don't set Content-Type — the browser sets multipart/form-data with
  // the right ``boundary`` automatically when you pass a FormData.
  // Reuse the same auth/error-handling shape as the JSON path.
  const url = DIRECT_BACKEND
    ? `${DIRECT_BACKEND}/api/v1${path}`
    : `${BASE}${path}`;     // dev fallback (proxy)
  const token = await getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(url, { method: 'POST', body: form, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = formatDetail(await res.json()) || res.statusText; }
    catch { try { detail = await res.text(); } catch { /* ignore */ } }
    throw new ApiError(res.status, detail || 'Upload failed', url);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get:    <T>(p: string) => request<T>(p),
  post:   <T>(p: string, body?: unknown) => request<T>(p, { method: 'POST',  body: JSON.stringify(body ?? {}) }),
  put:    <T>(p: string, body?: unknown) => request<T>(p, { method: 'PUT',   body: JSON.stringify(body ?? {}) }),
  patch:  <T>(p: string, body?: unknown) => request<T>(p, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
  del:    <T>(p: string) => request<T>(p, { method: 'DELETE' }),
  postForm: <T>(p: string, form: FormData) => requestForm<T>(p, form),
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
