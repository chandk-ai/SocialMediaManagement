'use client';
import { useEffect, useRef, useState } from 'react';
import { LogOut, Settings } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { getSupabase } from '@/lib/auth/supabase';
import type { User } from '@supabase/supabase-js';

export function UserMenu() {
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    const sb = getSupabase();
    sb.auth.getUser().then(({ data }) => setUser(data.user ?? null));
    const { data: sub } = sb.auth.onAuthStateChange((_e, session) => {
      setUser(session?.user ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, [open]);

  async function signOut() {
    const sb = getSupabase();
    await sb.auth.signOut();
    router.replace('/login');
    router.refresh();
  }

  const display = user?.user_metadata?.display_name || user?.email || 'User';
  const initials = display.split(/\s+|@/).slice(0, 2)
    .map((s: string) => s[0]?.toUpperCase()).join('');

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="size-8 rounded-full bg-accent text-accent-fg flex items-center justify-center text-xs font-medium"
        aria-label="Open user menu"
      >
        {initials || 'U'}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-56 card p-2 z-50 text-sm">
          <div className="px-3 py-2 border-b border-ink-200 mb-1">
            <div className="font-medium truncate">{display}</div>
            <div className="text-xs text-ink-500 truncate">{user?.email || '—'}</div>
          </div>
          <Link href="/settings" className="flex items-center gap-2 px-3 py-2 hover:bg-ink-100 rounded-lg">
            <Settings size={14} /> Settings
          </Link>
          <button
            onClick={signOut}
            className="w-full flex items-center gap-2 px-3 py-2 text-red-600 hover:bg-red-50 rounded-lg"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
