'use client';
/**
 * Top bar — hamburger (mobile), breadcrumbs, global search, bell, user menu.
 *
 * ``title`` is retained for back-compat with pages that haven't been
 * updated to rely on breadcrumbs alone. When supplied, it renders as
 * the page-title strong text above the breadcrumb row. When omitted,
 * the breadcrumbs stand on their own — which is the recommended path
 * since the last crumb IS the page title.
 *
 * Pages with dynamic detail labels (source name, workflow name, …)
 * pass ``crumbOverrides`` keyed by URL segment; see Breadcrumbs.tsx.
 */
import { Bell, Menu, Search } from 'lucide-react';
import { UserMenu } from './UserMenu';
import { Breadcrumbs } from './Breadcrumbs';

export function TopBar({
  title,
  crumbOverrides,
  onMenuClick,
}: {
  title?: string;
  crumbOverrides?: Record<string, string>;
  onMenuClick?: () => void;
}) {
  return (
    <header className="border-b border-ink-200 bg-white">
      <div className="px-4 sm:px-6 pt-2 pb-2 flex items-center gap-3">
        <button
          onClick={onMenuClick}
          aria-label="Open menu"
          className="lg:hidden -ml-1 p-2 text-ink-700 hover:bg-ink-100 rounded-lg"
        >
          <Menu size={18} />
        </button>
        <div className="min-w-0 flex-1">
          <Breadcrumbs overrides={crumbOverrides} />
          {title && (
            <h1 className="text-base font-semibold tracking-tight truncate mt-0.5">
              {title}
            </h1>
          )}
        </div>
        <div className="ml-auto flex items-center gap-3 shrink-0">
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
