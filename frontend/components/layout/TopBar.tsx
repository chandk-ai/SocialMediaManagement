'use client';
import { Bell, Menu, Search } from 'lucide-react';
import { UserMenu } from './UserMenu';

export function TopBar({ title, onMenuClick }: { title: string; onMenuClick?: () => void }) {
  return (
    <header className="border-b border-ink-200 bg-white">
      <div className="px-4 sm:px-6 h-14 flex items-center gap-3">
        <button
          onClick={onMenuClick}
          aria-label="Open menu"
          className="lg:hidden -ml-1 p-2 text-ink-700 hover:bg-ink-100 rounded-lg"
        >
          <Menu size={18} />
        </button>
        <h1 className="text-base font-semibold tracking-tight truncate">{title}</h1>
        <div className="ml-auto flex items-center gap-3">
          <div className="relative hidden md:block">
            <Search size={14} className="absolute left-3 top-2.5 text-ink-500" />
            <input
              className="input pl-8 w-48 lg:w-64"
              placeholder="Search workflows, posts…"
              aria-label="Search"
            />
          </div>
          <button
            className="btn-ghost size-9 p-0 rounded-full"
            aria-label="Notifications"
          >
            <Bell size={16} />
          </button>
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
