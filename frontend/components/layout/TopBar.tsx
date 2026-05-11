'use client';
/**
 * Top bar — hamburger (mobile), breadcrumbs, user menu.
 *
 * ``title`` is retained for back-compat with pages that haven't been
 * updated to rely on breadcrumbs alone. When supplied, it renders as
 * the page-title strong text above the breadcrumb row. When omitted,
 * the breadcrumbs stand on their own — which is the recommended path
 * since the last crumb IS the page title.
 *
 * Pages with dynamic detail labels (source name, workflow name, …)
 * pass ``crumbOverrides`` keyed by URL segment; see Breadcrumbs.tsx.
 *
 * Subtle frosted-glass backdrop on scroll-sticky use: the
 * ``bg-white/85`` + backdrop-blur gives the bar a sense of depth
 * over scrolled content without losing legibility. The bell and
 * global search were removed — neither was wired to a real backend.
 * When notifications + search become real features they get their
 * own dedicated UI, not a placeholder icon.
 */
import { Menu } from 'lucide-react';
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
    <header className="border-b border-ink-200/80 bg-white/85 backdrop-blur supports-[backdrop-filter]:bg-white/65 sticky top-0 z-30">
      <div className="px-4 sm:px-6 pt-2.5 pb-2.5 flex items-center gap-3">
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
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
