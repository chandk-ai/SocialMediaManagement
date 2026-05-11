'use client';

/**
 * /no-access — landing page for signed-in users with no org membership.
 *
 * The API client (lib/api/client.ts) redirects here on 403
 * `no_membership` responses, so authenticated-but-orphaned users
 * (typically: a fresh sign-in that hasn't been added to any
 * organization yet) get a useful explanation instead of a generic
 * "Unknown error" banner.
 *
 * Three exits:
 *   * Claim an invite (if they're expecting one) — open team page
 *   * Contact admin — generic mailto
 *   * Sign out — only fix when nobody's invited them
 *
 * Note: there's NO Sidebar/TopBar here because the user has no org
 * scope, so most of the nav would 403 again. Standalone shell only.
 */
import Link from 'next/link';
import { useEffect } from 'react';
import { ShieldAlert, Mail, LogOut, Users } from 'lucide-react';

export default function NoAccessPage() {
  // Clear any stale "redirect loop" state — if the API kept bouncing
  // them here, the URL hash from the prior page can confuse the user.
  useEffect(() => {
    if (typeof window !== 'undefined' && window.location.hash) {
      history.replaceState(null, '', '/no-access');
    }
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center p-8 bg-stone-50">
      <div className="card max-w-lg w-full text-center py-10 px-8 bg-white border rounded shadow-sm">
        <div className="mx-auto mb-3 size-12 rounded-full bg-amber-50 flex items-center justify-center text-amber-600">
          <ShieldAlert size={24} />
        </div>
        <h1 className="text-lg font-semibold mb-1">You're signed in, but not in any organization</h1>
        <p className="text-sm text-ink-600 mb-6">
          Workflows, sources, and posts all live inside an organization.
          Your account isn't a member of any org yet, so there's nothing
          to show.
        </p>

        <div className="space-y-3 text-left">
          <Section
            icon={<Users size={16}/>}
            title="Expecting an invite?"
            body={
              <>
                If a teammate sent you an invitation link, click it
                from your email. The invite will attach your account
                to their org and bring you straight to the dashboard.
              </>
            }
          />
          <Section
            icon={<Mail size={16}/>}
            title="Need an invite?"
            body={
              <>
                Ask the admin of the org you want to join to invite
                you by email — they can do this from the Team page
                inside their workspace.
              </>
            }
          />
          <Section
            icon={<LogOut size={16}/>}
            title="Wrong account?"
            body={
              <>
                If you signed in with the wrong identity (e.g. a
                personal account when your invite went to the work
                one), sign out and sign back in with the right one.
              </>
            }
          />
        </div>

        <div className="flex flex-wrap gap-2 justify-center mt-6 pt-4 border-t">
          <Link href="/api/auth/signout" className="btn btn-outline">
            <LogOut size={14}/> Sign out
          </Link>
          <a href="mailto:support@example.com?subject=SMMS%20org%20access" className="btn btn-ghost">
            <Mail size={14}/> Contact support
          </a>
        </div>
      </div>
    </div>
  );
}

function Section({ icon, title, body }: { icon: React.ReactNode; title: string; body: React.ReactNode }) {
  return (
    <div className="flex gap-3 items-start">
      <span className="size-7 rounded bg-stone-100 flex items-center justify-center shrink-0 text-ink-600">
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink-900">{title}</p>
        <p className="text-xs text-ink-600 leading-relaxed">{body}</p>
      </div>
    </div>
  );
}
