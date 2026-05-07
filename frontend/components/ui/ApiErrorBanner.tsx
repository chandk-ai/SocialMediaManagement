import { AlertCircle } from 'lucide-react';
import type { ApiError } from '@/lib/api/client';

export function ApiErrorBanner({ error, retry }: { error: ApiError | Error | undefined; retry?: () => void }) {
  if (!error) return null;
  const isApi = 'status' in error;
  const status = isApi ? (error as ApiError).status : '—';
  const detail = isApi ? (error as ApiError).detail : error.message;
  const url    = isApi ? (error as ApiError).url    : undefined;
  const hint   = inferHint(status, detail, url);

  return (
    <div className="card border-red-200 bg-red-50 text-red-900 p-4 mb-4 flex gap-3 items-start">
      <AlertCircle size={18} className="mt-0.5 shrink-0 text-red-600" />
      <div className="text-sm flex-1 min-w-0">
        <div className="font-medium">Couldn't reach the backend (status {status})</div>
        <div className="mt-1 text-red-800/80 break-words">{detail}</div>
        {url && <div className="mt-1 font-mono text-[11px] opacity-60 break-all">{url}</div>}
        {hint && <div className="mt-2 text-xs">{hint}</div>}
      </div>
      {retry && (
        <button onClick={retry} className="text-sm underline shrink-0">Retry</button>
      )}
    </div>
  );
}

function inferHint(status: number | string, detail: string, url?: string): string {
  if (status === 401) return 'Sign out and back in — your token may have expired or be missing the org_id claim.';
  if (status === 502 || status === 504) return 'Backend service is starting up (Render starter plan sleeps after idle). Wait ~30 s and retry.';
  if (status === 500) return 'Backend error — check Render logs for the FastAPI exception.';
  if (typeof status === 'string' || status === 0) {
    return 'Network failed before reaching Vercel. Verify BACKEND_URL + NEXT_PUBLIC_API_URL are set in Vercel project settings, then redeploy.';
  }
  if (status === 404 && url?.includes('/api/proxy/')) {
    return 'Proxy reached but backend route not found — make sure your Render deploy is the latest commit.';
  }
  return '';
}
