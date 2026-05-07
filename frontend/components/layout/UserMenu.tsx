'use client';
import { useEffect, useRef, useState } from 'react';
import { LogOut, Settings, User as UserIcon, BookOpen } from 'lucide-react';
import Link from 'next/link';
import { signOut, useSession } from 'next-auth/react';

export function UserMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { data: session } = useSession();
  const initials = (session?.user?.name || session?.user?.email || 'U')
    .split(/\s+/).slice(0, 2).map(s => s[0]?.toUpperCase()).join('');

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', onClick);
    return () => window.removeEventListener('mousedown', onClick);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="size-8 rounded-full bg-accent text-accent-fg flex items-center justify-center text-xs font-medium"
        aria-label="Open user menu"
      >
        {initials}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-56 card p-2 z-50 text-sm">
          <div className="px-3 py-2 border-b border-ink-200 mb-1">
            <div className="font-medium truncate">{session?.user?.name || 'Anonymous'}</div>
            <div className="text-xs text-ink-500 truncate">{session?.user?.email || '—'}</div>
          </div>
          <Link href="/onboarding" className="flex items-center gap-2 px-3 py-2 hover:bg-ink-100 rounded-lg">
            <BookOpen size={14} /> Onboarding
          </Link>
          <Link href="/settings" className="flex items-center gap-2 px-3 py-2 hover:bg-ink-100 rounded-lg">
            <Settings size={14} /> Settings
          </Link>
          <button
            onClick={() => signOut({ callbackUrl: '/login' })}
            className="w-full flex items-center gap-2 px-3 py-2 text-red-600 hover:bg-red-50 rounded-lg"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
