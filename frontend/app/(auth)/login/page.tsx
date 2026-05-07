'use client';
/**
 * Email + password login via Supabase Auth.
 *
 * The form uses `useSearchParams` (to honour `?callbackUrl=…`), which Next 14
 * requires inside a Suspense boundary when the page is statically prerendered.
 * The outer `LoginPage` is the boundary; `LoginForm` is the client island.
 */
import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { useToast } from '@/components/ui/Toast';
import { getSupabase } from '@/lib/auth/supabase';
import { signIn } from 'next-auth/react';

export const dynamic = 'force-dynamic';

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginShell />}>
      <LoginForm />
    </Suspense>
  );
}

function LoginShell({ children }: { children?: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 p-4">
      <div className="card w-full max-w-md p-8 text-center">
        <div className="mx-auto size-10 rounded-xl bg-accent mb-4" />
        <h1 className="text-xl font-semibold tracking-tight">Sign in to SMMS</h1>
        <p className="text-sm text-ink-500 mt-1">Use your work email + password.</p>
        {children}
      </div>
    </div>
  );
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const callbackUrl = params.get('callbackUrl') || '/dashboard';
  const { push } = useToast();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const showOkta = process.env.NEXT_PUBLIC_OKTA_ENABLED === 'true';

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const sb = getSupabase();
      const { error } = await sb.auth.signInWithPassword({ email, password });
      if (error) {
        push('error', error.message);
        return;
      }
      push('success', 'Signed in');
      router.replace(callbackUrl);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  async function onForgot() {
    if (!email) {
      push('info', 'Enter your email first');
      return;
    }
    const sb = getSupabase();
    const { error } = await sb.auth.resetPasswordForEmail(email, {
      redirectTo: window.location.origin + '/login',
    });
    push(error ? 'error' : 'success',
         error ? error.message : 'Reset email sent');
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 p-4">
      <form onSubmit={onSubmit} className="card w-full max-w-md p-8 text-center">
        <div className="mx-auto size-10 rounded-xl bg-accent mb-4" />
        <h1 className="text-xl font-semibold tracking-tight">Sign in to SMMS</h1>
        <p className="text-sm text-ink-500 mt-1">Use your work email + password.</p>

        <div className="mt-6 space-y-3 text-left">
          <div>
            <label className="block text-xs text-ink-500 mb-1">Email</label>
            <Input
              type="email" required autoComplete="email"
              value={email} onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label className="block text-xs text-ink-500 mb-1">Password</label>
            <Input
              type="password" required autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
          </div>
        </div>

        <Button type="submit" className="w-full mt-5" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>

        <button
          type="button"
          onClick={onForgot}
          className="block mx-auto mt-3 text-xs text-ink-500 hover:text-ink-700"
        >
          Forgot password?
        </button>

        {showOkta && (
          <>
            <div className="my-5 flex items-center gap-3 text-xs text-ink-500">
              <div className="flex-1 h-px bg-ink-200" />
              or
              <div className="flex-1 h-px bg-ink-200" />
            </div>
            <Button
              type="button" variant="outline" className="w-full"
              onClick={() => signIn('okta', { callbackUrl })}
            >
              Continue with Okta SSO
            </Button>
          </>
        )}

        <p className="text-xs text-ink-500 mt-6">
          Trouble signing in? Contact your workspace admin.
        </p>
      </form>
    </div>
  );
}
