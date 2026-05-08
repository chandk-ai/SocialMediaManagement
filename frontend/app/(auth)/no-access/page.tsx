'use client';
/**
 * "You're signed in but don't have a workspace yet" landing page.
 *
 * The backend's auth dependency returns a 403 with body
 * ``{detail: {code: "no_membership", message: "..."}}`` when a Supabase
 * sign-in succeeds but the user has no row in ``smms.users`` and no
 * pending invitation. We route the user here from the Providers layer
 * so they see something useful instead of a generic error banner.
 */
import Link from 'next/link';
import { Button } from '@/components/ui/Button';
import { getSupabase } from '@/lib/auth/supabase';
import { Mail, LogOut } from 'lucide-react';
import { useEffect, useState } from 'react';

export default function NoAccessPage() {
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const sb = getSupabase();
        const { data } = await sb.auth.getSession();
        setEmail(data.session?.user?.email ?? null);
      } catch { /* ignore */ }
    })();
  }, []);

  async function signOut() {
    try {
      const sb = getSupabase();
      await sb.auth.signOut();
    } finally {
      window.location.href = '/login';
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-50 p-4">
      <div className="card w-full max-w-md p-8 text-center">
        <div className="mx-auto size-12 rounded-full bg-amber-50 text-amber-600 flex items-center justify-center mb-4">
          <Mail size={22} />
        </div>
        <h1 className="text-xl font-semibold tracking-tight">You're signed in</h1>
        <p className="text-sm text-ink-500 mt-2">
          But you don't have access to a workspace yet. An admin needs to
          invite you with this email first:
        </p>
        {email && (
          <div className="mt-3 rounded-lg bg-ink-50 border border-ink-100 px-3 py-2 text-sm font-mono">
            {email}
          </div>
        )}
        <p className="text-sm text-ink-500 mt-4">
          Forward that to whoever runs your team's workspace and have them
          add you under <span className="font-medium">Settings → Team</span>.
        </p>

        <div className="mt-6 flex justify-center gap-2">
          <Button variant="outline" onClick={signOut}>
            <LogOut size={14} /> Sign out
          </Button>
          <Link href="/login" className="btn btn-primary">
            Try a different account
          </Link>
        </div>

        <p className="text-[11px] text-ink-400 mt-6">
          If you just bootstrapped this deployment, you should have become
          admin of a fresh workspace automatically. If not, your auth
          callback hit an error — try signing out and back in.
        </p>
      </div>
    </div>
  );
}
