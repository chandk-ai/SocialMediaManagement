'use client';
/**
 * OAuth callback handler. Providers redirect the browser here after consent.
 *
 * Flow:
 *   1. Read `code`, `state`, `platform_id` from the URL.
 *   2. Re-relay them to the FastAPI callback (which exchanges the code for
 *      access tokens and persists them encrypted on the Platform row).
 *      Going through the frontend lets us attach the Supabase bearer.
 *   3. Show success/error, redirect back to /platforms.
 */
import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import { api } from '@/lib/api/client';

export const dynamic = 'force-dynamic';

function CallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [state, setState] = useState<'loading' | 'ok' | 'err'>('loading');
  const [message, setMessage] = useState<string>('Finishing connection…');

  useEffect(() => {
    const code = params.get('code');
    const oauthState = params.get('state');
    const platformId =
      params.get('platform_id') ||
      sessionStorage.getItem('oauth_platform_id') ||
      '';

    // Provider error path (consent denied, scope refused, etc.)
    const providerError = params.get('error') || params.get('error_description');
    if (providerError) {
      setState('err');
      setMessage(`Provider rejected the request: ${providerError}`);
      return;
    }

    if (!code || !oauthState || !platformId) {
      setState('err');
      setMessage('Missing OAuth parameters in callback URL.');
      return;
    }

    (async () => {
      try {
        const qs = new URLSearchParams({ code, state: oauthState }).toString();
        const result = await api.get<{ status: string; account_id?: string }>(
          `/platforms/${platformId}/oauth/callback?${qs}`,
        );
        sessionStorage.removeItem('oauth_platform_id');
        setState('ok');
        setMessage(
          `Connected${result.account_id ? ` as ${result.account_id}` : ''}. ` +
          `Redirecting…`,
        );
        setTimeout(() => router.replace('/platforms'), 900);
      } catch (e: any) {
        setState('err');
        setMessage(`Connection failed: ${e?.detail || e?.message || 'unknown error'}`);
      }
    })();
  }, [params, router]);

  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-ink-50">
      <div className="card max-w-md w-full p-6 text-center bg-white">
        {state === 'loading' && (
          <>
            <Loader2 className="mx-auto mb-3 animate-spin text-ink-500" size={28} />
            <h1 className="text-lg font-semibold mb-1">Connecting account</h1>
          </>
        )}
        {state === 'ok' && (
          <>
            <CheckCircle2 className="mx-auto mb-3 text-emerald-600" size={32} />
            <h1 className="text-lg font-semibold mb-1">Connected</h1>
          </>
        )}
        {state === 'err' && (
          <>
            <AlertCircle className="mx-auto mb-3 text-red-600" size={32} />
            <h1 className="text-lg font-semibold mb-1">Couldn't connect</h1>
          </>
        )}
        <p className="text-sm text-ink-700 mt-1">{message}</p>
        {state === 'err' && (
          <button
            className="mt-5 text-sm underline"
            onClick={() => router.replace('/platforms')}
          >
            Back to Platforms
          </button>
        )}
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense fallback={<div />}>
      <CallbackInner />
    </Suspense>
  );
}
