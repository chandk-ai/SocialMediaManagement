'use client';
/**
 * Sign-in page — three methods, one screen:
 *
 *   1. Password    — email + password via Supabase signInWithPassword.
 *   2. Email link  — passwordless magic link via signInWithOtp. Supabase
 *                    emails a one-click sign-in link; createBrowserClient's
 *                    `detectSessionInUrl` consumes the returned hash/code
 *                    on landing so we don't need a separate /auth/callback.
 *   3. Okta SSO    — only shown when NEXT_PUBLIC_OKTA_ENABLED=true.
 *
 * useEffect listens for `auth.onAuthStateChange('SIGNED_IN')` so the magic
 * link flow auto-redirects the moment Supabase detects the session.
 *
 * `useSearchParams` requires a Suspense boundary in Next 14 when the page
 * is statically prerendered — outer LoginPage is the boundary; LoginForm
 * is the client island.
 */
import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { useToast } from '@/components/ui/Toast';
import { getSupabase } from '@/lib/auth/supabase';
import { signIn } from 'next-auth/react';
import { Loader2, Mail, Lock, CheckCircle2 } from 'lucide-react';
import { cn } from '@/lib/utils';

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
        <p className="text-sm text-ink-500 mt-1">Pick how you'd like to sign in.</p>
        {children}
      </div>
    </div>
  );
}

type Method = 'password' | 'magic';

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const callbackUrl = params.get('callbackUrl') || '/dashboard';
  const { push } = useToast();

  const [method, setMethod] = useState<Method>('magic');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [magicSent, setMagicSent] = useState(false);

  const showOkta = process.env.NEXT_PUBLIC_OKTA_ENABLED === 'true';

  // Auto-redirect when Supabase reports a fresh session — covers both the
  // post-login navigation AND the magic-link callback (the URL hash is
  // consumed by createBrowserClient on page load and fires SIGNED_IN).
  useEffect(() => {
    const sb = getSupabase();
    let cancelled = false;
    (async () => {
      const { data } = await sb.auth.getSession();
      if (!cancelled && data.session) {
        router.replace(callbackUrl);
        router.refresh();
      }
    })();
    const { data: sub } = sb.auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_IN') {
        router.replace(callbackUrl);
        router.refresh();
      }
    });
    return () => { cancelled = true; sub.subscription?.unsubscribe(); };
  }, [router, callbackUrl]);

  async function onPassword(e: React.FormEvent) {
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
    } finally { setBusy(false); }
  }

  async function onMagicLink(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const sb = getSupabase();
      const { error } = await sb.auth.signInWithOtp({
        email,
        options: {
          // Land back on /login — the auth-state listener above redirects
          // onward once createBrowserClient detects the session in the URL.
          emailRedirectTo: window.location.origin + '/login',
          // Allow first-time sign-in: if the email isn't a Supabase user
          // yet but has a pending invitation, the auth-claim path on the
          // backend will create their smms.users row.
          shouldCreateUser: true,
        },
      });
      if (error) {
        push('error', error.message);
        return;
      }
      setMagicSent(true);
    } finally { setBusy(false); }
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
      <div className="card w-full max-w-md p-8">
        <div className="text-center">
          <div className="mx-auto size-10 rounded-xl bg-accent mb-4" />
          <h1 className="text-xl font-semibold tracking-tight">Sign in to SMMS</h1>
          <p className="text-sm text-ink-500 mt-1">
            {method === 'magic'
              ? 'Email yourself a one-click sign-in link.'
              : 'Use your email and password.'}
          </p>
        </div>

        {/* Method tabs */}
        <div role="tablist" className="mt-5 grid grid-cols-2 rounded-lg bg-ink-100 p-1 text-sm">
          <button
            type="button"
            role="tab"
            aria-selected={method === 'magic'}
            onClick={() => { setMethod('magic'); setMagicSent(false); }}
            className={cn(
              'flex items-center justify-center gap-1.5 rounded-md py-1.5 transition-colors',
              method === 'magic' ? 'bg-white shadow-sm font-medium' : 'text-ink-500 hover:text-ink-700',
            )}
          >
            <Mail size={14} /> Email link
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={method === 'password'}
            onClick={() => setMethod('password')}
            className={cn(
              'flex items-center justify-center gap-1.5 rounded-md py-1.5 transition-colors',
              method === 'password' ? 'bg-white shadow-sm font-medium' : 'text-ink-500 hover:text-ink-700',
            )}
          >
            <Lock size={14} /> Password
          </button>
        </div>

        {/* Magic link */}
        {method === 'magic' && (
          magicSent ? (
            <div className="mt-6 text-center space-y-3">
              <CheckCircle2 size={32} className="mx-auto text-emerald-600" />
              <h2 className="text-base font-medium">Check your email</h2>
              <p className="text-sm text-ink-500">
                We sent a sign-in link to <span className="font-medium text-ink-700">{email}</span>.
                Click the link to finish signing in.
              </p>
              <button
                onClick={() => { setMagicSent(false); }}
                className="text-xs text-ink-500 hover:text-ink-700 underline"
              >
                Use a different email
              </button>
            </div>
          ) : (
            <form onSubmit={onMagicLink} className="mt-5 space-y-3 text-left">
              <div>
                <label className="block text-xs text-ink-500 mb-1">Email</label>
                <Input
                  type="email" required autoComplete="email"
                  value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <Button type="submit" className="w-full" disabled={busy || !email}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
                Send sign-in link
              </Button>
              <p className="text-[11px] text-ink-500 text-center">
                No password needed — we'll email you a one-click link.
              </p>
            </form>
          )
        )}

        {/* Password */}
        {method === 'password' && (
          <form onSubmit={onPassword} className="mt-5 space-y-3 text-left">
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
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : null}
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
            <button
              type="button"
              onClick={onForgot}
              className="block mx-auto mt-1 text-xs text-ink-500 hover:text-ink-700"
            >
              Forgot password?
            </button>
          </form>
        )}

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

        <p className="text-xs text-ink-500 mt-6 text-center">
          Trouble signing in? Contact your workspace admin.
        </p>
      </div>
    </div>
  );
}
